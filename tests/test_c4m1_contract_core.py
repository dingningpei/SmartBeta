"""C4M1-1 -- contract-core tests (synthetic only).

Authority: ``worker_tasks/campaign4_m1/task-c4m1-1-contract-core.md`` section
"Tests (synthetic only)".  Every test is offline and synthetic: candidate
payloads, factor panels, series records and C3 control series are hand-built;
no ``data_store`` path, production artifact, reserve / holdout, Knowledge-PIT
write or network path is touched, and no test calls ``git``.

Coverage (the eight frozen test rows):

1. A2 validation + the C3 normalizer accepting the stripped list;
2. A4 gate / family cap;
3. A6 restatement;
4. the rank statistic;
5. the test statistics;
6. the training-window firewall (renderers + gate);
7. the frozen prompt hashes and render determinism;
8. the v2.5 admission guard on the TD path.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import date, timedelta
from typing import Any

import pandas as pd
import pytest

from smart_beta.campaign import campaign3_launch as _launch
from smart_beta.campaign.campaign3_generation import build_campaign3_policy
from smart_beta.campaign4 import contract as C
from smart_beta.campaign4 import diversity as D
from smart_beta.campaign4 import feedback as F
from smart_beta.campaign4 import ranking as R
from smart_beta.dataset_v1 import pqv2_evaluation as E
from smart_beta.dataset_v1.pqv2_series import PQv2SeriesExport, PQv2SeriesRecord
from smart_beta.diagnostics import known_factors as KF
from smart_beta.research import llm_generator, llm_generator_deepseek
from smart_beta.research.generator import (
    GenerationEvent,
    RawArtifact,
    normalize_generation,
)
from smart_beta.spec.factor_spec import to_dict as spec_to_dict

_CUT = date(2018, 7, 31)


# ---------------------------------------------------------------------------
# synthetic helpers
# ---------------------------------------------------------------------------


def _dates(start: date, count: int, *, step_days: int = 30) -> list[date]:
    return [start + timedelta(days=step_days * index) for index in range(count)]


def _stocks(count: int = 40) -> list[str]:
    return [f"S{index:03d}" for index in range(count)]


def _panel(dates: list[date], stocks: list[str], fn: Any) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"date": day, "stock_id": stock, "value": float(fn(day, index))}
            for day in dates
            for index, stock in enumerate(stocks)
        ]
    )


def _momentum(day: date, index: int) -> float:
    return math.sin(index * 0.7) + 0.03 * (day.toordinal() % 17)


def _independent(day: date, index: int) -> float:
    return float((index * index * 13 + day.day * 7 + day.month * 3) % 29) - 14.0


def _valid_candidate(**overrides: Any) -> dict[str, Any]:
    candidate: dict[str, Any] = {
        "factor_spec": spec_to_dict(_launch.control_factor_spec()),
        "research_question": "does the event primitive predict the cross-section?",
        "economic_rationale": "synthetic rationale",
        "predicted_relation": {
            "variable": "earnings announcement return",
            "direction": "higher_return",
        },
        "direction_check": "a high expression value is the long side",
        "family": "earnings_event",
    }
    candidate.update(overrides)
    return candidate


def _payload(candidates: list[Any]) -> dict[str, Any]:
    return {
        "schema_version": C.CAMPAIGN4_SCHEMA,
        "candidates": candidates,
    }


def _series_record(
    cell: str,
    periods: list[date],
    values: list[float],
    *,
    status: str | None = None,
    reason: str | None = None,
) -> PQv2SeriesRecord:
    return PQv2SeriesRecord(
        cell=cell,
        estimand="L",
        scenario="TD",
        event_scenario=None,
        periods=tuple(periods),
        b_series=tuple(0.0 for _ in periods),
        l_series=tuple(float(value) for value in values),
        gross_series=tuple(0.0 for _ in periods),
        net_series=tuple(float(value) for value in values),
        status=status,
        reason=reason,
        declaration_hash="c" * 64,
    )


def _export(records: list[PQv2SeriesRecord]) -> PQv2SeriesExport:
    return PQv2SeriesExport(
        declaration_hash="c" * 64,
        diagnostics_hash="d" * 64,
        estimator=E.ESTIMATOR_IDENTITY,
        records=tuple(records),
    )


def _training_values(count: int = 30) -> list[float]:
    return [0.01 * ((index * 7) % 13 - 6) for index in range(count)]


# ---------------------------------------------------------------------------
# 1. A2 declarations
# ---------------------------------------------------------------------------


def test_a2_valid_entries_and_c3_normalizer_acceptance():
    raw = json.dumps(_payload([_valid_candidate()]))
    result = C.parse_campaign4_response(raw)

    assert result.n_a2_valid == 1
    assert result.n_a2_invalid == 0
    assert result.sidecar[0].valid
    assert result.sidecar[0].reason is None
    assert result.schema_version == C.CAMPAIGN3_SCHEMA

    document = result.stripped_document()
    assert document["schema_version"] == C.CAMPAIGN3_SCHEMA
    for candidate in document["candidates"]:
        for key in C.A2_KEYS:
            assert key not in candidate
        # The C3-required keys are untouched.
        assert "factor_spec" in candidate
        assert "research_question" in candidate
        assert "economic_rationale" in candidate

    # The unchanged C3 normalizer accepts the stripped list.
    policy = build_campaign3_policy(prompt_template_hash="a" * 64)
    content = json.dumps(
        document, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    event = GenerationEvent(
        invocation_ordinal=0,
        generator_identity=policy.generator_identity,
        generation_method=policy.generation_method,
        generation_policy_id=policy.content_hash,
        prompt_template_hash=policy.prompt_template_hash,
        history_snapshot_hash="b" * 64,
        seed=policy.seed,
        raw_artifact=RawArtifact(content=content),
    )
    outcome = normalize_generation(event, policy=policy)
    assert outcome.status.value == "normalized"
    rejected_reasons = [
        candidate.reason.value
        for candidate in outcome.candidates
        if candidate.reason is not None
    ]
    assert "unknown_field" not in rejected_reasons
    assert outcome.admitted_candidates


@pytest.mark.parametrize(
    "override,reason",
    [
        ({"predicted_relation": None}, "predicted_relation-not-object"),
        ({"predicted_relation": "not-an-object"}, "predicted_relation-not-object"),
        (
            {"predicted_relation": {"variable": "", "direction": "higher_return"}},
            "predicted_relation.variable-empty",
        ),
        (
            {"predicted_relation": {"variable": "   ", "direction": "higher_return"}},
            "predicted_relation.variable-empty",
        ),
        (
            {"predicted_relation": {"variable": 7, "direction": "higher_return"}},
            "predicted_relation.variable-empty",
        ),
        (
            {"predicted_relation": {"variable": "x", "direction": "sideways"}},
            "predicted_relation.direction-invalid",
        ),
        ({"direction_check": "  "}, "direction_check-empty"),
        ({"direction_check": 5}, "direction_check-empty"),
        ({"family": "not_a_family"}, "family-invalid"),
        ({"family": None}, "family-invalid"),
    ],
)
def test_a2_malformed_field_is_invalid_at_its_index(override, reason):
    payload = _payload([_valid_candidate(), _valid_candidate(**override)])
    raw = json.dumps(payload)
    result = C.parse_campaign4_response(raw)

    assert result.sidecar[0].valid
    assert not result.sidecar[1].valid
    assert result.sidecar[1].a2_status == C.A2_STATUS_INVALID
    assert result.sidecar[1].reason == reason
    # The invalid entry still travels through the stripped list and is counted.
    assert result.n_candidates == 2
    assert len(result.stripped_document()["candidates"]) == 2


def test_a2_missing_fields_are_invalid():
    base = _valid_candidate()
    no_a2 = {
        "factor_spec": base["factor_spec"],
        "research_question": "q",
        "economic_rationale": "r",
    }
    result = C.parse_campaign4_response(
        json.dumps(_payload([_valid_candidate(), no_a2]))
    )
    assert not result.sidecar[1].valid
    assert result.sidecar[1].reason == "predicted_relation-not-object"

    # Each individual missing A2 key is caught at its own index.
    for missing_key, reason in (
        ("predicted_relation", "predicted_relation-not-object"),
        ("direction_check", "direction_check-empty"),
        ("family", "family-invalid"),
    ):
        incomplete = _valid_candidate()
        del incomplete[missing_key]
        parsed = C.parse_campaign4_response(
            json.dumps(_payload([_valid_candidate(), incomplete]))
        )
        assert not parsed.sidecar[1].valid, missing_key
        assert parsed.sidecar[1].reason == reason, missing_key


def test_raw_response_bytes_and_sha_are_kept():
    raw = json.dumps(_payload([_valid_candidate()]))
    result = C.parse_campaign4_response(raw)
    assert result.raw_bytes == raw.encode("utf-8")
    assert result.raw_sha256 == hashlib.sha256(raw.encode("utf-8")).hexdigest()
    assert result.to_dict()["raw_sha256"] == result.raw_sha256


def test_top_level_schema_is_enforced():
    with pytest.raises(C.C4ContractError):
        C.parse_campaign4_response(
            json.dumps({"schema_version": "campaign3-candidates-v1", "candidates": []})
        )
    with pytest.raises(C.C4ContractError):
        C.parse_campaign4_response("not json")


# ---------------------------------------------------------------------------
# 2. A4 gate + family cap
# ---------------------------------------------------------------------------


def test_a4_near_duplicate_and_independent_admission():
    stocks = _stocks()
    dates = _dates(date(2017, 1, 1), 10) + _dates(date(2018, 8, 1), 3)
    panel_a = _panel(dates, stocks, _momentum)
    panel_neg = _panel(dates, stocks, lambda day, index: -_momentum(day, index))
    panel_ind = _panel(dates, stocks, _independent)

    gate = D.DiversityGate(window_cut=_CUT)
    first = gate.check("a", panel_a, "momentum")
    assert first.kind == D.ADMIT

    copied = gate.check("a-copy", panel_a, "momentum")
    assert copied.kind == D.NEAR_DUPLICATE
    assert copied.other_key == "a"
    assert copied.rho is not None and abs(copied.rho) > 0.9

    negated = gate.check("a-negated", panel_neg, "momentum")
    assert negated.kind == D.NEAR_DUPLICATE

    independent = gate.check("b", panel_ind, "momentum")
    assert independent.kind == D.ADMIT
    assert gate.admitted_keys == ("a", "b")


def test_a4_later_formations_do_not_affect_rho():
    stocks = _stocks()
    train = _dates(date(2017, 1, 1), 10)
    test = _dates(date(2018, 8, 1), 3)
    panel_a = _panel(train + test, stocks, _momentum)
    # Equal to A on the test dates, independent on the training dates.
    leak = _panel(
        train + test,
        stocks,
        lambda day, index: (
            _independent(day, index) if day < _CUT else _momentum(day, index)
        ),
    )
    gate = D.DiversityGate(window_cut=_CUT)
    assert gate.check("a", panel_a, "momentum").kind == D.ADMIT
    # If test dates counted, this would be a near-duplicate; they must not.
    assert gate.check("leak", leak, "momentum").kind == D.ADMIT

    # The converse: identical on the training dates, different on the test dates.
    same_train = _panel(
        train + test,
        stocks,
        lambda day, index: (
            _momentum(day, index) if day < _CUT else _independent(day, index)
        ),
    )
    gate2 = D.DiversityGate(window_cut=_CUT)
    assert gate2.check("a", panel_a, "momentum").kind == D.ADMIT
    assert gate2.check("same-train", same_train, "momentum").kind == D.NEAR_DUPLICATE


def test_a4_family_cap_counts_rejected_proposals():
    stocks = _stocks()
    dates = _dates(date(2017, 1, 1), 10)
    panel = _panel(dates, stocks, _momentum)
    gate = D.DiversityGate(window_cut=_CUT, family_cap=30)

    assert gate.check("m0", panel, "momentum").kind == D.ADMIT
    for index in range(1, 30):
        assert gate.check(f"m{index}", panel, "momentum").kind == D.NEAR_DUPLICATE
    assert gate.family_count("momentum") == 30

    cap = gate.check("m30", panel, "momentum")
    assert cap.kind == D.FAMILY_CAP
    assert cap.family == "momentum"
    assert gate.family_count("momentum") == 31


# ---------------------------------------------------------------------------
# 3. A6 restatement
# ---------------------------------------------------------------------------


def test_a6_restatement_self_match():
    stocks = _stocks()
    dates = _dates(date(2017, 1, 1), 10)
    panel = _panel(dates, stocks, _momentum)
    driver_panels = {"D-MOM": panel}
    gate = D.DiversityGate(window_cut=_CUT)
    driver_id, max_abs_e = gate.restatement(panel, driver_panels)
    assert driver_id == "D-MOM"
    assert max_abs_e == pytest.approx(1.0)


def test_a6_restatement_independent_is_low():
    stocks = _stocks()
    dates = _dates(date(2017, 1, 1), 10)
    panel = _panel(dates, stocks, _independent)
    driver_panels = {"D-MOM": _panel(dates, stocks, _momentum)}
    gate = D.DiversityGate(window_cut=_CUT)
    driver_id, max_abs_e = gate.restatement(panel, driver_panels)
    assert driver_id == "D-MOM"
    assert max_abs_e is not None and max_abs_e < 0.5


# ---------------------------------------------------------------------------
# 4. rank statistic
# ---------------------------------------------------------------------------


def test_rank_statistic_is_minimum_over_event_scenarios():
    train = _dates(date(2016, 1, 1), 30)
    values0 = _training_values()
    values1 = [0.02 * ((index * 3) % 11) - 0.10 for index in range(30)]
    export = _export(
        [
            _series_record("L-TD-K0", train, values0),
            _series_record("L-TD-K1", train, values1),
        ]
    )
    result = R.rank_statistic(export, _CUT)
    t0 = KF._ewc_statistic(train, values0, bound_alpha=0.05)[0]
    t1 = KF._ewc_statistic(train, values1, bound_alpha=0.05)[0]
    assert result.status == R.VALID
    assert result.value == pytest.approx(min(t0, t1))


def test_rank_statistic_truncates_to_training_and_matches_hand_slice():
    train = _dates(date(2016, 1, 1), 30)
    test = _dates(date(2018, 8, 1), 8)
    values = _training_values() + [0.05 * index for index in range(8)]
    record = _series_record("L-TD", train + test, values)
    export = _export([record])

    result = R.rank_statistic(export, _CUT)
    sliced_periods, sliced_values = R.window_series(record, _CUT, R.TRAINING)
    assert len(sliced_periods) == 30
    expected = KF._ewc_statistic(sliced_periods, sliced_values, bound_alpha=0.05)[0]
    assert result.status == R.VALID
    assert result.value == pytest.approx(expected)


def test_rank_statistic_propagates_not_assessed():
    train = _dates(date(2016, 1, 1), 30)
    record = _series_record(
        "L-TD", train, _training_values(), status="PIT_MISSING", reason="PIT_MISSING"
    )
    result = R.rank_statistic(_export([record]), _CUT)
    assert result.status == R.NOT_ASSESSED
    assert result.reason == "PIT_MISSING"
    assert result.value is None


def test_rank_statistic_insufficient_sample():
    train = _dates(date(2016, 1, 1), 23)
    record = _series_record("L-TD", train, _training_values(23))
    result = R.rank_statistic(_export([record]), _CUT)
    assert result.status == R.INSUFFICIENT_SAMPLE
    assert result.value is None


def test_l_t0_ewc_t_matches_hand_slice():
    train = _dates(date(2016, 1, 1), 30)
    values = _training_values()
    record = _series_record("L-T0", train, values)
    result = R.l_t0_ewc_t(_export([record]), _CUT)
    expected = KF._ewc_statistic(train, values, bound_alpha=0.05)[0]
    assert result.status == R.VALID
    assert result.value == pytest.approx(expected)


# ---------------------------------------------------------------------------
# 5. test statistics
# ---------------------------------------------------------------------------


def test_test_statistics_12_periods_is_descriptive_only():
    test = _dates(date(2018, 8, 1), 12)
    values = [0.01 * ((index % 5) - 2) for index in range(12)]
    result = R.test_statistics(_export([_series_record("L-TD", test, values)]), _CUT)
    assert result.ewc_t is None
    assert result.n == 12
    assert result.net_mean == pytest.approx(sum(values) / 12)
    assert result.net_sign == (1 if sum(values) > 0 else -1)
    assert result.positive_share == pytest.approx(
        sum(1 for value in values if value > 0) / 12
    )


def test_test_statistics_24_periods_has_a_t():
    test = _dates(date(2018, 8, 1), 24)
    values = _training_values(24)
    result = R.test_statistics(_export([_series_record("L-TD", test, values)]), _CUT)
    assert result.ewc_t is not None
    assert result.status == R.VALID
    expected = KF._ewc_statistic(test, values, bound_alpha=0.05)[0]
    assert result.ewc_t == pytest.approx(expected)


# ---------------------------------------------------------------------------
# 6. firewall
# ---------------------------------------------------------------------------


def test_renderer_and_gate_firewalls():
    train = _dates(date(2017, 1, 1), 10)
    stocks = _stocks()
    panel = _panel(train, stocks, _momentum)

    gate = D.DiversityGate(window_cut=_CUT)
    with pytest.raises(C.C4FirewallError):
        gate.check("x", panel, "momentum", period=date(2018, 8, 1))
    with pytest.raises(C.C4FirewallError):
        gate.restatement(
            panel, {"D-MOM": panel}, period=date(2018, 8, 1)
        )
    with pytest.raises(C.C4FirewallError):
        gate.assert_period(date(2018, 8, 1))

    record = F.FeedbackRecord(
        proposal_id="p1",
        factor_spec_hash="f" * 64,
        expression={"op": "field", "role": "tri"},
        family="momentum",
        predicted_direction="higher_return",
        status="EVALUATED",
        reason=None,
        rank_stat=1.0,
        rank=1,
        l_t0_ewc_t=0.5,
        l_td_ewc_t=0.6,
        restatement=("D-MOM", 1.0),
        nearest=None,
        period=date(2018, 8, 1),
    )
    with pytest.raises(C.C4FirewallError):
        F.render_c4_feedback([record], window_cut=_CUT)

    late_series = [F.ControlSeries("B-T0", (date(2018, 8, 1),), (0.1,))]
    with pytest.raises(C.C4FirewallError):
        F.render_control_prompt(None, late_series, window_cut=_CUT)


# ---------------------------------------------------------------------------
# 7. prompts
# ---------------------------------------------------------------------------


def test_frozen_prompt_hashes():
    assert F.PROMPT_TEMPLATE_C4_HASH == F.FROZEN_PROMPT_TEMPLATE_C4_SHA256
    assert F.PROMPT_TEMPLATE_C4_HASH == (
        "2d616f1689a487d1e5f039f2cc1ab310367b0c47901cf9d4f371d0bffa92d05e"
    )
    assert F.CONTROL_PROMPT_TEMPLATE_HASH == (
        llm_generator_deepseek.PROMPT_TEMPLATE_V2_HASH
    )
    assert F.C4_SYSTEM_PROMPT == llm_generator.SYSTEM_PROMPT.replace(
        "Campaign-3", "Campaign-4"
    )


def test_treatment_feedback_line_and_determinism():
    record = F.FeedbackRecord(
        proposal_id="p1",
        factor_spec_hash="f" * 64,
        expression={"op": "field", "role": "tri"},
        family="momentum",
        predicted_direction="higher_return",
        status="EVALUATED",
        reason=None,
        rank_stat=1.23456,
        rank=1,
        l_t0_ewc_t=0.5,
        l_td_ewc_t=0.6,
        restatement=("D-MOM", 0.98765),
        nearest=("p0", 0.91234),
        period=date(2018, 1, 1),
    )
    first = F.render_c4_feedback([record], window_cut=_CUT)
    second = F.render_c4_feedback([record], window_cut=_CUT)
    assert first == second
    line = json.loads(first)
    assert line["rank_stat"] == 1.235
    assert line["expression"] == json.dumps(
        {"op": "field", "role": "tri"},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    assert line["restatement"] == {"driver": "D-MOM", "abs_e": 0.988}
    assert line["nearest"] == {"proposal_id": "p0", "rho": 0.912}

    assert F.render_c4_feedback([], window_cut=_CUT) == "No prior proposals."


def test_control_prompt_is_deterministic():
    train = _dates(date(2016, 1, 1), 30)
    values = _training_values()
    series = [
        F.ControlSeries("B-T0", tuple(train), tuple(values)),
        F.ControlSeries("L-T0", tuple(train), tuple(values)),
        F.ControlSeries("B-TS", tuple(train), tuple(values)),
        F.ControlSeries("L-TS", tuple(train), tuple(values)),
    ]
    first = F.render_control_prompt(None, series, window_cut=_CUT)
    second = F.render_control_prompt(None, series, window_cut=_CUT)
    assert first == second
    # The C3 v2 template's history token is substituted (no raw token remains).
    assert llm_generator.VISIBLE_HISTORY_TOKEN not in first
    assert llm_generator.IS_FEEDBACK_TOKEN not in first


def test_c4_prompt_substitutes_the_feedback_token():
    rendered = F.render_c4_prompt("No prior proposals.")
    assert F.C4_FEEDBACK_TOKEN not in rendered
    assert "No prior proposals." in rendered


# ---------------------------------------------------------------------------
# 8. v2.5 admission guard
# ---------------------------------------------------------------------------


def test_td_path_calls_assert_v25_admitted(monkeypatch: pytest.MonkeyPatch):
    train = _dates(date(2016, 1, 1), 30)
    export = _export([_series_record("L-TD", train, _training_values())])

    def _blocked() -> float:
        raise C.C4ContractError("v2.5 blocked by the test")

    monkeypatch.setattr(R, "assert_v25_admitted", _blocked)
    with pytest.raises(C.C4ContractError):
        R.rank_statistic(export, _CUT)
    with pytest.raises(C.C4ContractError):
        R.test_statistics(export, _CUT)
