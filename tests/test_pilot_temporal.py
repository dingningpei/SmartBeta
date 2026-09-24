"""Pilot 1A P1A-G6R: the temporal information-flow firewall adversarial suite.

This module owns the required adversarial tests (A)-(F) of
``worker_tasks/pilot1/pilot1-plan.md`` section 26b. Every case is built from a
**real** sealed :class:`~smart_beta.evaluation.spec.EvaluationRecord` produced
by :func:`smart_beta.evaluation.engine.evaluate` over constructed offline data
loaded through the same P1A-G1 path as the frozen Gate-B fixtures (the G1/G2
helpers are reused read-only). No fixture under ``tests/fixtures`` is modified
and no hand-built record is used as the primary evidence.

The adversarial cases:

* **(A)** parameter-sensitivity rows computed including holdout dates -> FAIL;
* **(B)** a subperiod interval overlapping the holdout by one date -> FAIL;
* **(C)** a harmless-named table whose coverage includes the holdout -> FAIL;
* **(D)** all visible evidence ending before the holdout start -> PASS;
* **(E)** missing or unknown coverage metadata -> FAIL CLOSED;
* **(F)** a new generator-visible empirical category/key without a coverage
  declaration -> FAIL CLOSED.

The suite is offline: the shared ``offline_guard`` fixture blocks ``urlopen``,
``socket.connect`` and ``socket.create_connection`` and scrubs every data and
model credential.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
from pathlib import Path

import pytest
from pilot_support import offline_guard  # noqa: F401

from smart_beta.evaluation.engine import evaluate
from smart_beta.evaluation.spec import (
    EvaluationRecord,
    EvidenceTable,
    MetricKey,
    ParameterPoint,
    SubperiodRule,
)
from smart_beta.pilot.data import (
    DAILY_TOTAL_RETURN_REQUIREMENT,
    compute_fixture_hashes,
    load_pit_inputs,
)
from smart_beta.pilot.design import (
    PilotPartitionDates,
    build_frozen_evaluation_spec_template,
    build_partition,
)
from smart_beta.pilot.temporal import (
    COVERAGE_DECLARABLE_TABLES,
    TemporalFirewallViolation,
    TemporalVerdict,
    audit_temporal_firewall,
    enforce_temporal_firewall,
)
from smart_beta.research.history import (
    DevelopmentEvidenceRecord,
    GeneratorVisibleResearchHistory,
    ResearchFeedback,
    VisibleExperiment,
    VisibleFamily,
)
from smart_beta.research.policy import StopReason
from smart_beta.spec.engine import evaluate_factor
from smart_beta.spec.factor_spec import (
    FactorInput,
    FactorSpec,
    MissingPolicy,
)
from smart_beta.spec.requirements import Frequency

pytestmark = pytest.mark.usefixtures("offline_guard")

# ---------------------------------------------------------------------------
# constructed offline data (synthetic Tiingo-format fixtures)
# ---------------------------------------------------------------------------

_TICKERS = ("AAA", "BBB", "CCC", "DDD", "EEE", "FFF")
_START = "2025-09-05"
_END = "2026-09-15"
_DATE_CAP = "2026-07-01"
_TREE = "a" * 40

#: The config's authorized development interval ``D = [is_start, holdout_start)``.
_IS_START = dt.date(2025, 10, 15)
_HOLDOUT_START = dt.date(2026, 5, 1)

_PARTITION_DATES = PilotPartitionDates(
    is_start="2025-10-15",
    is_end="2026-02-27",
    oos_start="2026-03-02",
    oos_end="2026-04-30",
    holdout_start="2026-05-01",
    holdout_end="2026-06-30",
    warmup_start="2025-09-08",
    warmup_end="2025-10-14",
)

_EXPERIMENT_ID = "a" * 64
_HYPOTHESIS_ID = "b" * 64
_FAMILY_ID = "c" * 64

#: The section-26b development metric set (PARAMETER_SENSITIVITY removed).
_DEVELOPMENT_METRICS = (
    MetricKey.IC,
    MetricKey.RANK_IC,
    MetricKey.LONG_SHORT,
    MetricKey.SHARPE,
    MetricKey.MAX_DRAWDOWN,
    MetricKey.TURNOVER_COST_ADJUSTED,
    MetricKey.SUBPERIOD,
)

#: The only robustness table whose rows carry row-level temporal provenance.
_SUBPERIOD_TABLE = "subperiod_stability"


def _weekdays(start: str, end: str) -> list[dt.date]:
    current = dt.date.fromisoformat(start)
    last = dt.date.fromisoformat(end)
    days: list[dt.date] = []
    while current <= last:
        if current.weekday() < 5:
            days.append(current)
        current += dt.timedelta(days=1)
    return days


def _write_synthetic_fixture(
    root: Path, tickers: tuple[str, ...], start: str, end: str
) -> Path:
    """Write a synthetic Tiingo-format fixture directory with a manifest."""
    root.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, object] = {"recordings": {}}
    dates = _weekdays(start, end)
    for index, ticker in enumerate(tickers):
        meta = {"ticker": ticker, "name": f"{ticker} Inc", "exchangeCode": "NYSE"}
        meta_name = f"{ticker.lower()}_meta.json"
        (root / meta_name).write_text(json.dumps(meta), encoding="utf-8")
        manifest["recordings"][meta_name] = {  # type: ignore[index]
            "url_path": f"/tiingo/daily/{ticker}",
            "params": {},
            "status_code": 200,
        }
        rows = []
        price = 100.0 + index * 10.0
        for day_index, day in enumerate(dates):
            price *= 1.0 + 0.0005 * ((day_index % 5) - 2)
            rows.append(
                {
                    "date": day.isoformat() + "T00:00:00.000Z",
                    "close": round(price, 4),
                    "high": round(price * 1.01, 4),
                    "low": round(price * 0.99, 4),
                    "open": round(price * 0.995, 4),
                    "volume": 1_000_000 + day_index,
                    "adjClose": round(price, 4),
                    "adjHigh": round(price * 1.01, 4),
                    "adjLow": round(price * 0.99, 4),
                    "adjOpen": round(price * 0.995, 4),
                    "adjVolume": 1_000_000 + day_index,
                    "divCash": 0.0,
                    "splitFactor": 1.0,
                }
            )
        prices_name = f"{ticker.lower()}_eod.json"
        (root / prices_name).write_text(json.dumps(rows), encoding="utf-8")
        manifest["recordings"][prices_name] = {  # type: ignore[index]
            "url_path": f"/tiingo/daily/{ticker}/prices",
            "params": {"startDate": start, "endDate": end},
            "status_code": 200,
        }
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return root


@pytest.fixture(scope="module")
def pilot_data(tmp_path_factory):
    """The G1 adapter output over constructed offline fixtures."""
    root = tmp_path_factory.mktemp("temporal-fixture")
    fixture_dir = _write_synthetic_fixture(
        root / "fixture", _TICKERS, _START, _END
    )
    return load_pit_inputs(
        fixture_dir=fixture_dir,
        expected_fixture_hashes=compute_fixture_hashes(fixture_dir),
        universe=_TICKERS,
        start=_START,
        end=_END,
        requirement=DAILY_TOTAL_RETURN_REQUIREMENT,
        fixture_tree_id=_TREE,
        date_cap=_DATE_CAP,
    )


def _factor_spec() -> FactorSpec:
    return FactorSpec(
        id="temporal_identity",
        description="identity of the admitted daily total return",
        expression="ret",
        inputs=(FactorInput("ret", DAILY_TOTAL_RETURN_REQUIREMENT),),
        frequency=Frequency.DAILY,
        missing_policy=MissingPolicy.PROPAGATE,
    )


def _real_record(
    pilot_data,
    *,
    metrics: tuple[MetricKey, ...] = _DEVELOPMENT_METRICS,
    boundaries: tuple[dt.date, ...] = (_IS_START, dt.date(2026, 1, 2), _HOLDOUT_START),
):
    """Produce a real sealed ``EvaluationRecord`` through the sealed engine."""
    template = build_frozen_evaluation_spec_template(_PARTITION_DATES)
    spec_template = dataclasses.replace(
        template,
        metrics=tuple(metrics),
        parameter_grid=(
            ParameterPoint(
                n_groups=3, horizon=1, cost_bps=10.0, winsorization=0.01
            ),
        ),
        subperiod_rule=SubperiodRule(boundaries=tuple(boundaries)),
    )
    factor_spec = _factor_spec()
    inputs = {
        alias: pilot_data.trusted_input
        for alias in factor_spec.referenced_roles
    }
    engine_result = evaluate_factor(factor_spec, inputs)
    spec = dataclasses.replace(
        spec_template, factor_provenance_hash=engine_result.content_hash
    )
    partition = build_partition(_PARTITION_DATES)
    return evaluate(
        engine_result.evaluation.panel,
        spec,
        pilot_data.realized_returns,
        partition,
        periods_per_year=252,
    )


def _visible_for(
    record,
    *,
    robustness_tables=None,
    fold_evidence=None,
) -> GeneratorVisibleResearchHistory:
    evidence = DevelopmentEvidenceRecord.from_evaluation_record(
        record, experiment_id=_EXPERIMENT_ID
    )
    experiment = VisibleExperiment(
        experiment_id=_EXPERIMENT_ID,
        hypothesis_id=_HYPOTHESIS_ID,
        family_id=_FAMILY_ID,
        factor_provenance_hash=record.factor_provenance_hash,
        evaluation_spec_hash=record.spec_hash,
        fold_evidence=(
            evidence.fold_evidence if fold_evidence is None else fold_evidence
        ),
        redundancy=evidence.redundancy,
        robustness_tables=(
            evidence.robustness_tables
            if robustness_tables is None
            else robustness_tables
        ),
    )
    return GeneratorVisibleResearchHistory(
        experiments=(experiment,),
        families=(VisibleFamily(family_id=_FAMILY_ID, consumed_slots=1),),
    )


def _feedback_for(visible: GeneratorVisibleResearchHistory) -> ResearchFeedback:
    return ResearchFeedback.from_visible(
        visible,
        (
            "is_metrics",
            "oos_metrics",
            "robustness_evidence",
            "search_governance_status",
            "holdout_independent_reason_classes",
        ),
    )


def _audit(visible, record, feedback=None):
    return audit_temporal_firewall(
        visible,
        feedback,
        [record],
        is_start=_IS_START,
        holdout_start=_HOLDOUT_START,
    )


def _failing_categories(audit) -> set[str]:
    return {row.category for row in audit.failures}


# ---------------------------------------------------------------------------
# (D) all visible evidence ending before the holdout start -> PASS
# ---------------------------------------------------------------------------


def test_case_d_evidence_before_holdout_start_passes(pilot_data):
    record = _real_record(pilot_data)
    visible = _visible_for(record)
    feedback = _feedback_for(visible)
    audit = _audit(visible, record, feedback)

    assert audit.status is TemporalVerdict.PASS, audit.findings
    assert audit.findings == ()
    # Fold items and every subperiod row were actually covered.
    assert any(row.category == "fold" for row in audit.rows)
    assert any(row.category == "subperiod" for row in audit.rows)
    assert not audit.failures
    # The OOS fold ends exactly at the authorized holdout start (zero holdout
    # observations), which is legal under TF-3.
    oos_ends = {
        row.source_end_exclusive
        for row in audit.rows
        if row.category == "fold" and row.source_end_exclusive is not None
    }
    assert _HOLDOUT_START.isoformat() in oos_ends


# ---------------------------------------------------------------------------
# (A) parameter-sensitivity rows computed including holdout dates -> FAIL
# ---------------------------------------------------------------------------


def test_case_a_parameter_sensitivity_including_holdout_fails(pilot_data):
    metrics = tuple(
        sorted(
            {*_DEVELOPMENT_METRICS, MetricKey.PARAMETER_SENSITIVITY},
            key=lambda member: member.value,
        )
    )
    record = _real_record(pilot_data, metrics=metrics)
    # The raw sealed record really does carry parameter-sensitivity rows over
    # the full alignment (the section-26b leak).
    assert record.parameter_sensitivity_table.rows
    visible = _visible_for(record)
    feedback = _feedback_for(visible)
    audit = _audit(visible, record, feedback)

    assert audit.status is TemporalVerdict.FAIL
    assert "parameter_sensitivity" in _failing_categories(audit)
    for row in audit.failures:
        if row.category == "parameter_sensitivity":
            assert row.evaluation_record_hash == record.content_hash


# ---------------------------------------------------------------------------
# (B) subperiod interval overlapping the holdout by one date -> FAIL
# ---------------------------------------------------------------------------


def test_case_b_subperiod_overlapping_holdout_by_one_date_fails(pilot_data):
    boundaries = (_IS_START, dt.date(2026, 1, 2), _HOLDOUT_START + dt.timedelta(days=1))
    record = _real_record(pilot_data, boundaries=boundaries)
    # A real holdout-overlapping subperiod row.
    assert any(
        row[2] > _HOLDOUT_START.isoformat()
        for row in record.subperiod_table.rows
    )
    visible = _visible_for(record)
    feedback = _feedback_for(visible)
    audit = _audit(visible, record, feedback)

    assert audit.status is TemporalVerdict.FAIL
    assert "subperiod" in _failing_categories(audit)
    overlaps = [
        row
        for row in audit.failures
        if row.category == "subperiod"
        and row.source_end_exclusive == (
            _HOLDOUT_START + dt.timedelta(days=1)
        ).isoformat()
    ]
    assert overlaps
    assert all(row.evaluation_record_hash == record.content_hash for row in overlaps)


# ---------------------------------------------------------------------------
# (C) harmless-named table whose coverage includes the holdout -> FAIL
# ---------------------------------------------------------------------------


def test_case_c_unknown_named_table_with_rows_fails(pilot_data):
    record = _real_record(pilot_data)
    leaky_table = EvidenceTable(
        name="regime_stability",
        columns=("start", "end", "value", "n_obs"),
        rows=(("2025-10-15", "2026-06-01", 0.1, 5),),
    )
    evidence = DevelopmentEvidenceRecord.from_evaluation_record(
        record, experiment_id=_EXPERIMENT_ID
    )
    visible = _visible_for(
        record,
        robustness_tables=evidence.robustness_tables + (leaky_table,),
    )
    audit = _audit(visible, record)

    assert audit.status is TemporalVerdict.FAIL
    assert "unknown_table" in _failing_categories(audit)
    assert "regime_stability" not in COVERAGE_DECLARABLE_TABLES


# ---------------------------------------------------------------------------
# (E) missing or unknown coverage metadata -> FAIL CLOSED
# ---------------------------------------------------------------------------


def test_case_e_unknown_fold_key_fails_closed(pilot_data):
    record = _real_record(pilot_data)
    visible = _visible_for(record)
    payload = visible.to_dict()
    payload["experiments"][0]["fold_evidence"].append(
        {"fold_key": "mystery#0", "role": "is", "metrics": []}
    )
    audit = audit_temporal_firewall(
        payload,
        None,
        [record],
        is_start=_IS_START,
        holdout_start=_HOLDOUT_START,
    )

    assert audit.status is TemporalVerdict.FAIL
    assert "fold" in _failing_categories(audit)
    unknown = [
        row
        for row in audit.failures
        if row.category == "fold" and row.item_key == "mystery#0"
    ]
    assert unknown and unknown[0].source_start is None


def test_case_e_subperiod_missing_coverage_columns_fails_closed(pilot_data):
    record = _real_record(pilot_data)
    evidence = DevelopmentEvidenceRecord.from_evaluation_record(
        record, experiment_id=_EXPERIMENT_ID
    )
    no_coverage = EvidenceTable(
        name=_SUBPERIOD_TABLE,
        columns=("value", "n_obs"),
        rows=((0.1, 5),),
    )
    visible = _visible_for(
        record,
        robustness_tables=tuple(
            no_coverage if table.name == _SUBPERIOD_TABLE else table
            for table in evidence.robustness_tables
        ),
    )
    audit = _audit(visible, record)

    assert audit.status is TemporalVerdict.FAIL
    assert "subperiod" in _failing_categories(audit)


# ---------------------------------------------------------------------------
# (F) a new generator-visible empirical category/key -> FAIL CLOSED
# ---------------------------------------------------------------------------


def test_case_f_new_visible_experiment_key_fails_closed(pilot_data):
    record = _real_record(pilot_data)
    visible = _visible_for(record)
    payload = visible.to_dict()
    payload["experiments"][0]["novel_robustness_category"] = {"rows": [[1, 2]]}
    audit = audit_temporal_firewall(
        payload,
        None,
        [record],
        is_start=_IS_START,
        holdout_start=_HOLDOUT_START,
    )

    assert audit.status is TemporalVerdict.FAIL
    assert "schema" in _failing_categories(audit)
    assert any(
        row.item_key == "visible_history.experiments.novel_robustness_category"
        for row in audit.failures
    )


def test_case_f_new_feedback_key_fails_closed(pilot_data):
    record = _real_record(pilot_data)
    visible = _visible_for(record)
    feedback_payload = _feedback_for(visible).to_dict()
    feedback_payload["experiments"][0]["novel_category"] = [{"rows": [[1]]}]
    audit = audit_temporal_firewall(
        None,
        feedback_payload,
        [record],
        is_start=_IS_START,
        holdout_start=_HOLDOUT_START,
    )

    assert audit.status is TemporalVerdict.FAIL
    assert "schema" in _failing_categories(audit)


# ---------------------------------------------------------------------------
# TF-2 provenance: visible rows/items must be present in the sealed record
# ---------------------------------------------------------------------------


def test_spoofed_subperiod_row_absent_from_sealed_record_fails(pilot_data):
    """A visible subperiod row narrowed to lie inside D but not in the record fails."""
    record = _real_record(pilot_data)
    visible = _visible_for(record)
    payload = visible.to_dict()
    tables = payload["experiments"][0]["robustness_tables"]
    subperiod = next(table for table in tables if table["name"] == _SUBPERIOD_TABLE)
    columns = list(subperiod["columns"])
    key_index = columns.index("subperiod_key")
    start_index = columns.index("subperiod_start")
    end_index = columns.index("subperiod_end")
    first = list(subperiod["rows"][0])
    # Narrow the interval so it trivially satisfies containment in D while
    # not being a row of the sealed record's subperiod_table.
    first[start_index] = _IS_START.isoformat()
    first[end_index] = "2025-12-01"
    assert first[start_index] < first[end_index] < _HOLDOUT_START.isoformat()
    subperiod["rows"][0] = first

    audit = audit_temporal_firewall(
        payload,
        None,
        [record],
        is_start=_IS_START,
        holdout_start=_HOLDOUT_START,
    )

    assert audit.status is TemporalVerdict.FAIL
    assert "subperiod" in _failing_categories(audit)
    spoofed = [
        row
        for row in audit.failures
        if row.category == "subperiod" and row.item_key == str(first[key_index])
    ]
    assert spoofed
    # Coverage is never taken from the visible row.
    assert spoofed[0].source_start is None
    assert spoofed[0].source_end_exclusive is None
    assert "absent" in spoofed[0].detail


def test_visible_fold_metrics_mismatch_fails(pilot_data):
    """A visible fold item whose metrics differ from the sealed record fails."""
    record = _real_record(pilot_data)
    visible = _visible_for(record)
    payload = visible.to_dict()
    fold_items = payload["experiments"][0]["fold_evidence"]
    assert fold_items
    target = fold_items[0]
    metrics = target["metrics"]
    assert metrics
    original = metrics[0]["value"]
    metrics[0]["value"] = 1.0 if original is None else original + 1.0

    audit = audit_temporal_firewall(
        payload,
        None,
        [record],
        is_start=_IS_START,
        holdout_start=_HOLDOUT_START,
    )

    assert audit.status is TemporalVerdict.FAIL
    assert "fold" in _failing_categories(audit)
    mismatch = [
        row
        for row in audit.failures
        if row.category == "fold" and row.item_key == target["fold_key"]
    ]
    assert mismatch
    assert "metrics" in mismatch[0].detail


def test_feedback_fold_metrics_mismatch_fails(pilot_data):
    """The same provenance rule covers ResearchFeedback fold items."""
    record = _real_record(pilot_data)
    visible = _visible_for(record)
    feedback_payload = _feedback_for(visible).to_dict()
    experiment = feedback_payload["experiments"][0]
    fold_key = experiment["is_folds"][0]["fold_key"]
    experiment["is_folds"][0]["metrics"] = []

    audit = audit_temporal_firewall(
        None,
        feedback_payload,
        [record],
        is_start=_IS_START,
        holdout_start=_HOLDOUT_START,
    )

    assert audit.status is TemporalVerdict.FAIL
    assert "fold" in _failing_categories(audit)
    assert any(
        row.category == "fold" and row.item_key == fold_key
        for row in audit.failures
    )


def test_sealed_provenance_match_survives_journal_json_round_trip(pilot_data):
    """The post-hoc path must match rows/metrics after JSON serialization."""
    record = _real_record(pilot_data)
    visible = _visible_for(record)
    feedback = _feedback_for(visible)
    record_payload = json.loads(json.dumps(record.to_dict()))
    visible_payload = json.loads(json.dumps(visible.to_dict()))
    feedback_payload = json.loads(json.dumps(feedback.to_dict()))
    rebuilt = EvaluationRecord.from_dict(record_payload)

    audit = audit_temporal_firewall(
        visible_payload,
        feedback_payload,
        [rebuilt],
        is_start=_IS_START,
        holdout_start=_HOLDOUT_START,
    )
    assert audit.status is TemporalVerdict.PASS, audit.findings
    assert not audit.failures


# ---------------------------------------------------------------------------
# TF-1 authorized-interval cross-check
# ---------------------------------------------------------------------------


def test_authorized_interval_cross_check_rejects_partition_mismatch(pilot_data):
    # A real record built against the *real-run* partition (holdout 2026-07-01)
    # audited against the dry-run D (holdout 2026-05-01) must fail TF-1.
    real_run_dates = PilotPartitionDates(
        is_start="2025-10-15",
        is_end="2026-03-31",
        oos_start="2026-04-01",
        oos_end="2026-06-30",
        holdout_start="2026-07-01",
        holdout_end="2026-09-15",
        warmup_start="2025-09-08",
        warmup_end="2025-10-14",
    )
    template = build_frozen_evaluation_spec_template(real_run_dates)
    spec_template = dataclasses.replace(
        template,
        metrics=_DEVELOPMENT_METRICS,
        parameter_grid=(
            ParameterPoint(
                n_groups=3, horizon=1, cost_bps=10.0, winsorization=0.01
            ),
        ),
        subperiod_rule=SubperiodRule(
            boundaries=(_IS_START, dt.date(2026, 1, 2), dt.date(2026, 7, 1))
        ),
    )
    factor_spec = _factor_spec()
    inputs = {
        alias: pilot_data.trusted_input
        for alias in factor_spec.referenced_roles
    }
    engine_result = evaluate_factor(factor_spec, inputs)
    spec = dataclasses.replace(
        spec_template, factor_provenance_hash=engine_result.content_hash
    )
    record = evaluate(
        engine_result.evaluation.panel,
        spec,
        pilot_data.realized_returns,
        build_partition(real_run_dates),
        periods_per_year=252,
    )
    visible = _visible_for(record)
    audit = _audit(visible, record)

    assert audit.status is TemporalVerdict.FAIL
    assert "authorized_interval_holdout_start" in _failing_categories(audit)
    assert "authorized_interval_is_start" not in _failing_categories(audit)


# ---------------------------------------------------------------------------
# enforcement: typed stop reason, no call
# ---------------------------------------------------------------------------


def test_enforce_raises_typed_stop_reason_on_violation(pilot_data):
    metrics = tuple(
        sorted(
            {*_DEVELOPMENT_METRICS, MetricKey.PARAMETER_SENSITIVITY},
            key=lambda member: member.value,
        )
    )
    record = _real_record(pilot_data, metrics=metrics)
    visible = _visible_for(record)
    with pytest.raises(TemporalFirewallViolation) as excinfo:
        enforce_temporal_firewall(
            visible,
            _feedback_for(visible),
            [record],
            is_start=_IS_START,
            holdout_start=_HOLDOUT_START,
        )
    assert excinfo.value.stop_reason is StopReason.HOLDOUT_FIREWALL_VIOLATION
    assert excinfo.value.audit is not None
    assert excinfo.value.audit.status is TemporalVerdict.FAIL
    assert excinfo.value.findings


def test_enforce_returns_audit_when_clean(pilot_data):
    record = _real_record(pilot_data)
    visible = _visible_for(record)
    audit = enforce_temporal_firewall(
        visible,
        _feedback_for(visible),
        [record],
        is_start=_IS_START,
        holdout_start=_HOLDOUT_START,
    )
    assert audit.status is TemporalVerdict.PASS


# ---------------------------------------------------------------------------
# closed schema round-trip: the frozen projections themselves are accepted
# ---------------------------------------------------------------------------


def test_closed_schema_accepts_the_frozen_projections(pilot_data):
    record = _real_record(pilot_data)
    visible = _visible_for(record)
    feedback = _feedback_for(visible)
    # to_dict() round-trips back through the sealed from_dict constructors,
    # proving the audit's allowlist matches the frozen P9-B schema.
    assert GeneratorVisibleResearchHistory.from_dict(visible.to_dict()) == visible
    assert ResearchFeedback.from_dict(feedback.to_dict()) == feedback
    audit = _audit(visible, record, feedback)
    assert audit.status is TemporalVerdict.PASS


def test_audit_rows_carry_every_tf6_field(pilot_data):
    record = _real_record(pilot_data)
    visible = _visible_for(record)
    audit = _audit(visible, record, _feedback_for(visible))
    assert audit.rows
    required = {
        "experiment_id",
        "evaluation_record_hash",
        "category",
        "item_key",
        "source_start",
        "source_end_exclusive",
        "authorized_start",
        "authorized_end_exclusive",
        "verdict",
    }
    for row in audit.rows:
        payload = row.to_dict()
        assert required <= set(payload)
        assert payload["authorized_start"] == _IS_START.isoformat()
        assert payload["authorized_end_exclusive"] == _HOLDOUT_START.isoformat()
        assert payload["verdict"] in {"PASS", "FAIL"}


def test_missing_source_record_fails_closed(pilot_data):
    record = _real_record(pilot_data)
    visible = _visible_for(record)
    audit = audit_temporal_firewall(
        visible,
        None,
        [],  # no source EvaluationRecord supplied
        is_start=_IS_START,
        holdout_start=_HOLDOUT_START,
    )
    assert audit.status is TemporalVerdict.FAIL
    assert "coverage" in _failing_categories(audit)
    assert any(row.category == "fold" for row in audit.failures)
