"""C4M1-2 -- C4 executor + walk-forward harness tests (synthetic only).

Authority: ``worker_tasks/campaign4_m1/task-c4m1-2-walkforward-harness.md``
section "Tests (synthetic only)".  Every test is offline: a fixed in-process
generator, a synthetic assembly and deterministic per-period series.  No
``data_store`` path, production artifact, reserve / holdout, Knowledge-PIT
write or network path is touched, and no test calls ``git``.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from smart_beta.campaign4 import generation as GEN
from smart_beta.campaign4 import walkforward as W
from smart_beta.campaign4.contract import C4FirewallError
from smart_beta.campaign4.feedback import (
    CONTROL_PROMPT_TEMPLATE_HASH,
    PROMPT_TEMPLATE_C4_HASH,
)
from smart_beta.research import llm_generator, llm_generator_deepseek
from tests.fixtures.c4m1.walkforward import synthetic as S

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _candidates(*, invalid_a2_index: int | None = None) -> list[dict]:
    """Four distinct-panel candidates, with an optional A2-invalid entry."""

    families = ("momentum", "momentum", "reversal", "reversal")
    keys = ("alpha", "alpha", "beta", "alpha")
    lags = (21, 22, 23, 24)
    out: list[dict] = []
    for index in range(4):
        out.append(
            S.c4_candidate(
                S.spec_dict(keys[index], numerator_lag=lags[index], denominator_lag=252),
                family=families[index],
                invalid_a2=(invalid_a2_index == index),
            )
        )
    return out


def _two_each() -> list[dict]:
    return _candidates()[:2]


def _make_deps(
    *,
    treatment_responses: list[str] | None = None,
    control_responses: list[str] | None = None,
    treatment: S.FakeCompleter | None = None,
    control: S.FakeCompleter | None = None,
    builder: S.SyntheticEvaluationBuilder | None = None,
):
    builder = builder or S.SyntheticEvaluationBuilder()
    treatment = treatment or S.FakeCompleter(
        treatment_responses
        if treatment_responses is not None
        else [S.c4_response(_two_each())]
    )
    control = control or S.FakeCompleter(
        control_responses
        if control_responses is not None
        else [S.c3_response([S.c3_candidate(c["factor_spec"]) for c in _two_each()])]
    )
    completers = {GEN.TREATMENT: treatment, GEN.CONTROL: control}
    deps = W.WalkforwardDeps(
        is_formations=S.IS_FORMATIONS,
        evaluate=builder,
        panel_builder=lambda spec: S.make_panel(S.IS_FORMATIONS, S.panel_key(spec)),
        make_llm=lambda arm: completers[arm],
        driver_panels={
            "D-MOM": S.make_panel(S.IS_FORMATIONS, "driver-mom"),
        },
        price_table=None,
    )
    return deps, builder, treatment, control


# ---------------------------------------------------------------------------
# 1. end to end
# ---------------------------------------------------------------------------


def test_end_to_end_selection_precedes_test_and_report_is_byte_identical(
    monkeypatch, tmp_path
):
    order: list[str] = []
    real_test = W.test_statistics

    def spy_test(td_export, cut):
        order.append("test")
        return real_test(td_export, cut)

    real_write = W.write_selection_file

    def spy_write(path, payload):
        order.append("selection")
        return real_write(path, payload)

    monkeypatch.setattr(W, "test_statistics", spy_test)
    monkeypatch.setattr(W, "write_selection_file", spy_write)

    deps, builder, treatment, control = _make_deps()
    report_one = W.run_walkforward(
        deps=deps,
        run_root=tmp_path / "run1",
        output_dir=tmp_path / "out1",
        origins=("W1",),
        max_proposal_budget=4,
    )
    assert report_one["runs"], "expected at least one run"
    assert order[0] == "selection"
    assert order[1] == "test"

    deps_two, _, _, _ = _make_deps()
    report_two = W.run_walkforward(
        deps=deps_two,
        run_root=tmp_path / "run2",
        output_dir=tmp_path / "out2",
        origins=("W1",),
        max_proposal_budget=4,
    )
    assert json.dumps(report_one, sort_keys=True) == json.dumps(
        report_two, sort_keys=True
    )
    assert report_one["content_sha256"] == report_two["content_sha256"]


def test_report_files_are_written(tmp_path):
    deps, _, _, _ = _make_deps()
    W.run_walkforward(
        deps=deps,
        run_root=tmp_path / "run",
        output_dir=tmp_path / "out",
        origins=("W1", "W2"),
        max_proposal_budget=4,
    )
    assert (tmp_path / "out" / "c4_walkforward_report.json").is_file()
    assert (tmp_path / "out" / "c4_walkforward_report.md").is_file()
    assert (tmp_path / "out" / "selection_treatment_W1.json").is_file()
    body = json.loads((tmp_path / "out" / "c4_walkforward_report.json").read_text())
    assert body["trial_accounting"]["total"] > 0
    # W1's test window is exactly 24 periods: the EWC t is computable.
    w1 = [run for run in body["runs"] if run["origin"] == "W1"]
    assert w1
    for run in w1:
        for row in run["test_statistics_top5"]:
            assert row["descriptive_only"] is False
            assert row["test_statistics"]["ewc_t"] is not None
    # W2's test window is 12 periods: descriptive only.
    w2 = [run for run in body["runs"] if run["origin"] == "W2"]
    assert w2
    for run in w2:
        for row in run["test_statistics_top5"]:
            assert row["descriptive_only"] is True
            assert row["test_statistics"]["ewc_t"] is None


def test_harness_refuses_data_store_paths(tmp_path):
    deps, _, _, _ = _make_deps()
    with pytest.raises(W.WalkforwardError):
        W.run_walkforward(
            deps=deps,
            run_root=tmp_path / "data_store" / "run",
            output_dir=tmp_path / "out",
            origins=("W1",),
            max_proposal_budget=2,
        )


# ---------------------------------------------------------------------------
# 2. firewall
# ---------------------------------------------------------------------------


def test_generator_prompts_contain_no_test_period(tmp_path):
    deps, _, treatment, control = _make_deps(
        treatment_responses=[S.c4_response(_two_each())],
        control_responses=[
            S.c3_response([S.c3_candidate(c["factor_spec"]) for c in _two_each()])
        ],
    )
    W.run_walkforward(
        deps=deps,
        run_root=tmp_path / "run",
        output_dir=tmp_path / "out",
        origins=("W1",),
        max_proposal_budget=3,
    )
    for _, user_prompt in treatment.prompts + control.prompts:
        assert "2018-07-31" not in user_prompt
        assert "2019-07-31" not in user_prompt
        # A general scan: no ISO date at/after the window cut may appear.
        import re

        for match in re.finditer(r"\b(\d{4}-\d{2}-\d{2})\b", user_prompt):
            assert date.fromisoformat(match.group(1)) < W.W1_CUT
    assert W.W1_CUT == date(2018, 7, 31)


def test_leaking_renderer_is_refused(monkeypatch, tmp_path):
    def leaky_control(visible, feedback, *, window_cut):
        return "leaked test period 2018-08-01"

    monkeypatch.setattr(GEN, "render_control_prompt", leaky_control)
    deps, _, _, _ = _make_deps()
    with pytest.raises(C4FirewallError):
        W.run_walkforward(
            deps=deps,
            run_root=tmp_path / "run",
            output_dir=tmp_path / "out",
            arms=("control",),
            origins=("W1",),
            max_proposal_budget=2,
        )


def test_no_oos_sealed_or_reserve_path_is_opened(monkeypatch, tmp_path):
    opened: list[str] = []
    real_open = Path.open

    def spy_open(self, *args, **kwargs):
        opened.append(str(self))
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", spy_open)
    deps, _, _, _ = _make_deps()
    W.run_walkforward(
        deps=deps,
        run_root=tmp_path / "run",
        output_dir=tmp_path / "out",
        origins=("W1",),
        max_proposal_budget=2,
    )
    forbidden = {"oos", "sealed", "reserve", "holdout", "data_store"}
    for path in opened:
        parts = set(Path(path).parts)
        assert not (parts & forbidden), f"opened forbidden path {path}"


# ---------------------------------------------------------------------------
# 3. budget
# ---------------------------------------------------------------------------


def _run_treatment(tmp_path, candidates, *, family_cap=30, budget=4, **kwargs):
    deps, builder, treatment, _ = _make_deps(
        treatment_responses=[S.c4_response(candidates)]
    )
    result = GEN.run_generation(
        arm=GEN.TREATMENT,
        policy=GEN.build_campaign4_policy(
            prompt_template_hash=PROMPT_TEMPLATE_C4_HASH,
            max_proposal_budget=budget,
        ),
        window_cut=W.W1_CUT,
        llm=treatment,
        evaluate=builder,
        panel_builder=lambda spec: S.make_panel(
            S.IS_FORMATIONS, S.panel_key(spec)
        ),
        driver_panels={"D-MOM": S.make_panel(S.IS_FORMATIONS, "driver-mom")},
        family_cap=family_cap,
        **kwargs,
    )
    return result, builder


def test_a2_invalid_near_duplicate_and_family_cap_count_but_are_not_evaluated(tmp_path):
    result, builder = _run_treatment(
        tmp_path, _candidates(invalid_a2_index=2), family_cap=1, budget=4
    )
    statuses = [entry.status for entry in result.entries]
    assert result.n_entries == 4
    assert statuses.count("EVALUATED") == 1
    assert statuses.count("FAMILY_CAP") == 1
    assert statuses.count("A2_DECLARATION_INVALID") == 1
    assert statuses.count("NEAR_DUPLICATE") == 1
    # Only the one admitted candidate was evaluated.
    assert len(builder.calls) == 1
    assert result.n_evaluated == 1


def test_zero_entry_response_counts_as_one():
    # A C4 payload with an empty candidate list still charges one budget unit.
    deps, builder, treatment, _ = _make_deps(
        treatment_responses=[S.c4_response([])]
    )
    result = GEN.run_generation(
        arm=GEN.TREATMENT,
        policy=GEN.build_campaign4_policy(
            prompt_template_hash=PROMPT_TEMPLATE_C4_HASH,
            max_proposal_budget=1,
        ),
        window_cut=W.W1_CUT,
        llm=treatment,
        evaluate=builder,
        panel_builder=lambda spec: S.make_panel(S.IS_FORMATIONS, "x"),
        family_cap=30,
    )
    assert result.n_entries == 1
    assert result.stop_reason == "proposal_budget_exhausted"
    assert result.entries == ()


def test_surplus_entries_beyond_the_budget_are_truncated_not_mislabelled():
    candidates = _candidates()
    deps, builder, treatment, _ = _make_deps(
        treatment_responses=[S.c4_response(candidates)]
    )
    result = GEN.run_generation(
        arm=GEN.TREATMENT,
        policy=GEN.build_campaign4_policy(
            prompt_template_hash=PROMPT_TEMPLATE_C4_HASH,
            max_proposal_budget=1,
        ),
        window_cut=W.W1_CUT,
        llm=treatment,
        evaluate=builder,
        panel_builder=lambda spec: S.make_panel(
            S.IS_FORMATIONS, S.panel_key(spec)
        ),
        driver_panels={"D-MOM": S.make_panel(S.IS_FORMATIONS, "driver-mom")},
    )
    assert result.n_entries == 1
    assert result.n_budget_truncated == 3
    assert [entry.status for entry in result.entries] == ["EVALUATED"]


def test_not_certified_candidate_is_counted_and_never_evaluated():
    spec = S.spec_dict("gamma", numerator_lag=30, denominator_lag=253, sign=-1)
    candidate = S.c4_candidate(spec, family="momentum")
    deps, builder, treatment, _ = _make_deps(
        treatment_responses=[S.c4_response([candidate])]
    )
    result = GEN.run_generation(
        arm=GEN.TREATMENT,
        policy=GEN.build_campaign4_policy(
            prompt_template_hash=PROMPT_TEMPLATE_C4_HASH,
            max_proposal_budget=1,
        ),
        window_cut=W.W1_CUT,
        llm=treatment,
        evaluate=builder,
        panel_builder=lambda spec: S.make_panel(
            S.IS_FORMATIONS, S.panel_key(spec)
        ),
        driver_panels={"D-MOM": S.make_panel(S.IS_FORMATIONS, "driver-mom")},
    )
    assert result.n_entries == 1
    assert [entry.status for entry in result.entries] == ["NOT_CERTIFIED"]
    assert len(builder.calls) == 0


def test_generator_exception_stops_the_run():
    failing = S.FakeCompleter([S.c4_response(_two_each())], raise_on=(0,))
    deps, builder, _, _ = _make_deps(treatment=failing)
    result = GEN.run_generation(
        arm=GEN.TREATMENT,
        policy=GEN.build_campaign4_policy(
            prompt_template_hash=PROMPT_TEMPLATE_C4_HASH,
            max_proposal_budget=10,
        ),
        window_cut=W.W1_CUT,
        llm=failing,
        evaluate=builder,
        panel_builder=lambda spec: S.make_panel(S.IS_FORMATIONS, "x"),
    )
    assert result.stop_reason == "generator_failure"
    assert result.n_entries == 0
    # no retry / no resample: exactly one call
    assert len(failing.prompts) == 1


# ---------------------------------------------------------------------------
# 4. cache
# ---------------------------------------------------------------------------


def test_same_spec_hash_is_evaluated_once(tmp_path):
    builder = S.SyntheticEvaluationBuilder()
    deps, _, treatment, control = _make_deps(
        builder=builder,
        treatment_responses=[S.c4_response(_two_each())],
        control_responses=[
            S.c3_response([S.c3_candidate(c["factor_spec"]) for c in _two_each()])
        ],
    )
    W.run_walkforward(
        deps=deps,
        run_root=tmp_path / "run",
        output_dir=tmp_path / "out",
        origins=("W1", "W2"),
        arms=("treatment", "control"),
        max_proposal_budget=2,
    )
    assert len(builder.calls) == len(set(builder.calls))
    assert len(builder.calls) == 2


# ---------------------------------------------------------------------------
# 5. caps
# ---------------------------------------------------------------------------


def test_token_cap_stops_the_run():
    many = _two_each()
    completer = S.FakeCompleter(
        [S.c4_response(many)], tokens_used=1000, cost_used=0.0
    )
    deps, builder, _, _ = _make_deps(treatment=completer)
    result = GEN.run_generation(
        arm=GEN.TREATMENT,
        policy=GEN.build_campaign4_policy(
            prompt_template_hash=PROMPT_TEMPLATE_C4_HASH,
            max_proposal_budget=100,
            max_llm_token_budget=1500,
        ),
        window_cut=W.W1_CUT,
        llm=completer,
        evaluate=builder,
        panel_builder=lambda spec: S.make_panel(
            S.IS_FORMATIONS, S.panel_key(spec)
        ),
    )
    assert result.stop_reason == "llm_token_budget_exhausted"


def test_usd_cap_stops_the_run():
    from smart_beta.research.llm_generator import PriceTable

    many = _two_each()
    completer = S.FakeCompleter(
        [S.c4_response(many)], tokens_used=100, cost_used=15.0
    )
    deps, builder, _, _ = _make_deps(treatment=completer)
    result = GEN.run_generation(
        arm=GEN.TREATMENT,
        policy=GEN.build_campaign4_policy(
            prompt_template_hash=PROMPT_TEMPLATE_C4_HASH,
            max_proposal_budget=100,
            max_llm_cost_budget=20.0,
        ),
        window_cut=W.W1_CUT,
        llm=completer,
        evaluate=builder,
        panel_builder=lambda spec: S.make_panel(
            S.IS_FORMATIONS, S.panel_key(spec)
        ),
        price_table=PriceTable(input_per_mtok=1.0, output_per_mtok=1.0),
    )
    assert result.stop_reason == "llm_cost_budget_exhausted"
    assert result.cost_used is not None and result.cost_used >= 20.0


# ---------------------------------------------------------------------------
# 6. control arm
# ---------------------------------------------------------------------------


def test_control_arm_uses_c3_v2_and_same_selection():
    assert W._arm_template_hash(GEN.CONTROL) == CONTROL_PROMPT_TEMPLATE_HASH
    assert CONTROL_PROMPT_TEMPLATE_HASH == llm_generator_deepseek.PROMPT_TEMPLATE_V2_HASH

    builder = S.SyntheticEvaluationBuilder()
    deps, _, _, control = _make_deps(builder=builder)
    result = GEN.run_generation(
        arm=GEN.CONTROL,
        policy=GEN.build_campaign4_policy(
            prompt_template_hash=W._arm_template_hash(GEN.CONTROL),
            max_proposal_budget=2,
        ),
        window_cut=W.W1_CUT,
        llm=control,
        evaluate=builder,
        panel_builder=lambda spec: S.make_panel(S.IS_FORMATIONS, "x"),
    )
    statuses = {entry.status for entry in result.entries}
    assert "NEAR_DUPLICATE" not in statuses
    assert "FAMILY_CAP" not in statuses
    assert "A2_DECLARATION_INVALID" not in statuses
    assert statuses <= {"EVALUATED", "NOT_ASSESSED"}
    # The control prompt is the frozen C3 v2 template and the C3 system prompt.
    system_prompt, user_prompt = control.prompts[0]
    assert system_prompt == llm_generator.SYSTEM_PROMPT
    assert "Campaign-3" in user_prompt
    # The same m = 1 rule applies.
    selected = W.select_candidate(result.entries)
    assert selected is not None and selected.status == "EVALUATED"


# ---------------------------------------------------------------------------
# 7. K-PIT
# ---------------------------------------------------------------------------


def test_executor_runs_with_kpit_none_and_writes_no_knowledge_log(monkeypatch, tmp_path):
    calls: list[str] = []

    def forbidden(*args, **kwargs):
        calls.append("kpit")
        raise AssertionError("a knowledge-log write was attempted")

    monkeypatch.setattr(
        "smart_beta.science.adapters.record_generation_input", forbidden
    )
    monkeypatch.setattr(
        "smart_beta.science.adapters.record_hypothesis_freeze", forbidden
    )
    deps, builder, treatment, _ = _make_deps()
    result = GEN.run_generation(
        arm=GEN.TREATMENT,
        policy=GEN.build_campaign4_policy(
            prompt_template_hash=PROMPT_TEMPLATE_C4_HASH,
            max_proposal_budget=2,
        ),
        window_cut=W.W1_CUT,
        llm=treatment,
        evaluate=builder,
        panel_builder=lambda spec: S.make_panel(
            S.IS_FORMATIONS, S.panel_key(spec)
        ),
    )
    assert calls == []
    assert result.manifest["kpit_record_hashes"] == []


# ---------------------------------------------------------------------------
# 8. origins
# ---------------------------------------------------------------------------


def test_origin_splits_on_synthetic_54_fold():
    training_w1, test_w1 = W.assert_origin_counts(S.IS_FORMATIONS, "W1")
    assert (len(training_w1), len(test_w1)) == (30, 24)
    assert max(training_w1) < W.W1_CUT <= min(test_w1)

    training_w2, test_w2 = W.assert_origin_counts(S.IS_FORMATIONS, "W2")
    assert (len(training_w2), len(test_w2)) == (42, 12)
    assert max(training_w2) < W.W2_CUT <= min(test_w2)


def test_wrong_origin_count_raises():
    with pytest.raises(W.WalkforwardError):
        W.assert_origin_counts(S.IS_FORMATIONS[:-1], "W1")
    wrong = tuple(sorted(S.IS_FORMATIONS)[:29]) + tuple(
        date(2018, 8, 15) for _ in range(25)
    )
    with pytest.raises(W.WalkforwardError):
        W.assert_origin_counts(wrong, "W1")


def test_select_candidate_ties_go_to_the_earlier_proposal():
    from tests.fixtures.c4m1.walkforward.synthetic import RankStatistic

    def entry(pid, value):
        stat = RankStatistic(
            status="VALID",
            value=value,
            reason=None,
            scenarios=(),
            window_cut=W.W1_CUT,
            last_period=None,
        )
        return GEN.GenerationEntry(
            proposal_id=pid,
            factor_spec_hash="f" * 64,
            expression={},
            family=None,
            predicted_direction=None,
            status="EVALUATED",
            reason=None,
            rank_stat=stat,
            l_t0_ewc_t=stat,
            l_td_ewc_t=stat,
        )

    assert W.select_candidate([entry("a", 1.0), entry("b", 1.0)]).proposal_id == "a"
    assert W.select_candidate([entry("a", 0.5), entry("b", 1.0)]).proposal_id == "b"
    assert W.select_candidate([]) is None


# ---------------------------------------------------------------------------
# 9. DeepSeek transport (reused by composition; no network)
# ---------------------------------------------------------------------------


def test_deepseek_transport_reuses_the_c3_adapter():
    from types import SimpleNamespace

    from smart_beta.campaign4.deepseek import Campaign4DeepSeekTransport
    from smart_beta.research.llm_generator import PriceTable

    calls: list[dict] = []
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content='{"ok":1}'),
                finish_reason="stop",
            )
        ],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
        model="deepseek-v4-flash",
    )

    class _Completions:
        def create(self, **kwargs):
            calls.append(kwargs)
            return response

    client = SimpleNamespace(chat=SimpleNamespace(completions=_Completions()))
    transport = Campaign4DeepSeekTransport(
        client=client,
        price_table=PriceTable(input_per_mtok=1.0, output_per_mtok=2.0),
    )
    out = transport.complete("SYSTEM", "USER")

    assert calls[0]["model"] == "deepseek-v4-flash"
    assert calls[0]["max_tokens"] == 32000
    assert calls[0]["reasoning_effort"] == "low"
    assert calls[0]["messages"] == [
        {"role": "system", "content": "SYSTEM"},
        {"role": "user", "content": "USER"},
    ]
    assert out.raw_artifact.content == '{"ok":1}'
    assert out.tokens_used == 15
    assert out.cost_used == (10 * 1.0 + 5 * 2.0) / 1_000_000
    with pytest.raises(NotImplementedError):
        transport.generate(None, None)
