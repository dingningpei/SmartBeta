"""C4M1-3 -- selection-rule study tests (synthetic only).

Authority: ``worker_tasks/campaign4_m1/task-c4m1-3-selection-study.md`` section
"Tests (synthetic only; ``tests/test_c4m1_selection_study.py``; no network, no
real data)".  Every test is offline and in-process: scripted per-period series,
synthetic M1-e-shaped run states produced by the real C4 executor path, and a
synthetic M1-e-shaped report for the reproduction check.  No ``data_store``
path, production artifact, reserve / holdout, Knowledge-PIT write or network
path is touched, and no test calls ``git``.
"""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from smart_beta.campaign4 import selection_study as SS
from smart_beta.campaign4.contract import C4FirewallError
from smart_beta.campaign4.ranking import rank_statistic
from smart_beta.campaign4.ranking import test_statistics as frozen_test_statistics
from smart_beta.spec.factor_spec import FactorSpec, factor_spec_hash
from tests.fixtures.c4m1.selection_study import synthetic as S
from tests.fixtures.c4m1.walkforward import synthetic as W


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _load_script():
    name = "c4_selection_study_script"
    if name in sys.modules:
        return sys.modules[name]
    path = (
        Path(__file__).resolve().parents[1] / "scripts" / "run_c4_selection_study.py"
    )
    loader_spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(loader_spec)
    sys.modules[name] = module
    loader_spec.loader.exec_module(module)
    return module


def _hash_tree(root: Path) -> dict[str, str]:
    if not root.exists():
        return {}
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _ranked(
    proposal_id: str,
    values,
    *,
    cells=None,
    cut=S.W1_CUT,
    formations=S.IS_FORMATIONS,
) -> SS.RankedCandidate:
    """A hand-built ranked candidate (no cache, no evaluation)."""

    key = hashlib.sha256(proposal_id.encode("utf-8")).hexdigest()
    series = S.candidate_series(
        values,
        key=key,
        cells=cells,
        formations=formations,
    )
    spec = FactorSpec.from_dict(S.spec_dict(f"rk{proposal_id}", numerator_lag=21))
    return SS.RankedCandidate(
        candidate=SS.PoolCandidate(
            proposal_id=proposal_id,
            proposal_order=0,
            factor_spec=spec,
            factor_spec_hash=key,
        ),
        series=series,
        rank_stat=rank_statistic(series, cut),
        s0=SS.s0_stat(series, cut),
        s1=SS.s1_stat(series, cut),
        halves_positive=SS.halves_positive(series, cut),
    )


def _ordered(ranked, proposal_ids):
    """Re-stamp the hand-built candidates with distinct proposal orders."""

    rebuilt = []
    for order, (item, proposal_id) in enumerate(zip(ranked, proposal_ids)):
        rebuilt.append(
            SS.RankedCandidate(
                candidate=SS.PoolCandidate(
                    proposal_id=proposal_id,
                    proposal_order=order,
                    factor_spec=item.candidate.factor_spec,
                    factor_spec_hash=item.candidate.factor_spec_hash,
                ),
                series=item.series,
                rank_stat=item.rank_stat,
                s0=item.s0,
                s1=item.s1,
                halves_positive=item.halves_positive,
            )
        )
    return rebuilt


def _run_study(
    env,
    base: Path,
    *,
    cache=None,
    m1e_reports=(),
    run_names=None,
):
    cache = cache or SS.SeriesCache(base / "cache", evaluate=env.builder)
    deps = SS.SelectionStudyDeps(
        m1e_run_root=env.run_root,
        cache=cache,
        output_dir=base / "out",
        run_names=run_names,
        m1e_reports=tuple(m1e_reports),
    )
    report, exit_code = SS.run_selection_study(deps=deps)
    return report, exit_code, base / "out"


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    """The four synthetic M1-e-shaped runs, built once through the real executor."""

    root = tmp_path_factory.mktemp("c4m1s")
    scripts: dict[str, dict] = {}
    for origin in ("W1", "W2"):
        scripts.update(S.pool_scripts(origin))
    builder = S.ScriptedBuilder(scripts=scripts)
    run_root = root / "runs"
    created = S.make_four_runs(run_root, builder=builder)
    return SimpleNamespace(
        root=root, run_root=run_root, runs=created, builder=builder
    )


# ---------------------------------------------------------------------------
# 1. the pool
# ---------------------------------------------------------------------------


def test_pool_reconstruction_reads_exactly_the_evaluated_proposals(tmp_path):
    builder = S.ScriptedBuilder()
    run_dir = S.make_six_status_run(tmp_path / "treatment_W1", builder=builder)
    state = json.loads((run_dir / "executor_state.json").read_text())

    pool = SS.load_run_pool(run_dir)

    # The state carries all six M1-e statuses; only EVALUATED is pooled.
    evaluated = state["evaluated"]
    reasons = {
        entry.get("not_assessed_reason")
        for entry in evaluated.values()
        if entry.get("not_assessed_reason") is not None
    }
    not_certified = [
        pid for pid, ok in state["certified"].items() if ok is not True
    ]
    assert len(not_certified) == 1  # NOT_CERTIFIED
    assert any(reason.startswith("NEAR_DUPLICATE") for reason in reasons)
    assert "MALFORMED_RESPONSE" in reasons  # SCHEMA_REJECTED
    assert "SYNTHETIC_NOT_ASSESSED" in reasons  # NOT_ASSESSED
    # A4 FAMILY_CAP persists the *family name* as the refusal reason.
    assert "reversal" in reasons
    assert pool.status_counts() == {
        "EVALUATED": 4,
        "NEAR_DUPLICATE": 1,
        "NOT_ASSESSED": 2,
        "NOT_CERTIFIED": 1,
        "SCHEMA_REJECTED": 1,
    }
    assert pool.arm == "treatment"
    assert pool.origin == "W1"
    assert pool.window_cut == S.W1_CUT

    # Exactly the proposals the run labelled EVALUATED, in proposal order.
    by_order = {item["proposal_id"]: index for index, item in enumerate(state["proposals"])}
    eligible = [item["proposal_id"] for item in state["proposals"]]
    expected = [
        pid
        for pid in eligible
        if state["certified"].get(pid) is True
        and pid in state["evaluated"]
        and state["evaluated"][pid]["assessed"] is True
        and state["evaluated"][pid]["not_assessed_reason"] is None
    ]
    assert [item.proposal_id for item in pool.proposals] == expected
    assert [item.proposal_order for item in pool.proposals] == [
        by_order[pid] for pid in expected
    ]
    assert [item.proposal_order for item in pool.proposals] == sorted(
        item.proposal_order for item in pool.proposals
    )
    # The rejected proposals keep the reason the state records, and none is
    # pooled.
    excluded = {
        pid: (status, reason)
        for pid, status, reason in pool.statuses
        if status != SS.EVALUATED
    }
    assert set(excluded).isdisjoint({item.proposal_id for item in pool.proposals})
    assert any(
        str(reason).startswith("NEAR_DUPLICATE") for _status, reason in excluded.values()
    )
    # The persisted spec hash is bound to the rebuilt spec.
    for item in pool.proposals:
        assert factor_spec_hash(item.factor_spec) == item.factor_spec_hash
        assert item.factor_spec_hash == state["evaluated"][item.proposal_id][
            "factor_spec_hash"
        ]


def test_pool_loader_refuses_an_ambiguous_state(tmp_path):
    builder = S.ScriptedBuilder()
    good = S.make_six_status_run(tmp_path / "treatment_W1", builder=builder)
    state = json.loads((good / "executor_state.json").read_text())

    foreign = tmp_path / "treatment_W2"
    foreign.mkdir()
    (foreign / "executor_state.json").write_text(
        json.dumps({**state, "schema": "campaign3-executor-write-ahead-v1"}),
        encoding="utf-8",
    )
    with pytest.raises(SS.SelectionStudyError, match="not the C4 write-ahead"):
        SS.load_run_pool(foreign)

    unnamed = tmp_path / "no_origin_here" / "state"
    unnamed.mkdir(parents=True)
    (unnamed / "executor_state.json").write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(SS.SelectionStudyError, match="unknown origin|arm/origin"):
        SS.load_run_pool(unnamed)

    stripped = tmp_path / "control_W1"
    stripped.mkdir()
    without = {key: value for key, value in state.items() if key != "evaluated"}
    (stripped / "executor_state.json").write_text(
        json.dumps(without), encoding="utf-8"
    )
    with pytest.raises(SS.SelectionStudyError, match="no 'evaluated' field"):
        SS.load_run_pool(stripped)


# ---------------------------------------------------------------------------
# 2. the statistics
# ---------------------------------------------------------------------------


def test_s0_stat_equals_rank_statistic_bitwise():
    series = S.candidate_series(
        S.shaped_series(first=-0.02, second=0.05, test=-0.01)
    )
    frozen = rank_statistic(series, S.W1_CUT)
    assert frozen.status == "VALID"
    assert SS.s0_stat(series, S.W1_CUT) == frozen.value
    assert repr(SS.s0_stat(series, S.W1_CUT)) == repr(frozen.value)

    # The event (K0/K1) form: the min over the scenarios, at both origins.
    event = S.candidate_series(
        (),
        cells={
            "L-TD-K0": S.shaped_series(first=0.01, second=0.02, test=0.0),
            "L-TD-K1": S.shaped_series(first=-0.01, second=0.03, test=0.01),
        },
    )
    for cut in (S.W1_CUT, S.W2_CUT):
        frozen = rank_statistic(event, cut)
        assert frozen.status == "VALID"
        assert SS.s0_stat(event, cut) == frozen.value


def test_s1_with_the_full_window_equals_s0():
    series = S.candidate_series(
        S.shaped_series(first=-0.03, second=0.04, test=0.01)
    )
    for cut in (S.W1_CUT, S.W2_CUT):
        training = sum(1 for day in S.IS_FORMATIONS if day < cut)
        assert SS.s1_stat(series, cut, last_n=training) == SS.s0_stat(series, cut)


def test_s1_uses_the_last_24_training_periods():
    series = S.candidate_series(
        S.shaped_series(first=-0.06, second=0.10, test=0.0)
    )
    # The last 24 training periods are much stronger than the full window.
    assert SS.s1_stat(series, S.W1_CUT) > SS.s0_stat(series, S.W1_CUT)

    # Cross-check against the frozen path on a series truncated to that tail.
    training = [day for day in S.IS_FORMATIONS if day < S.W1_CUT]
    tail_periods = tuple(training[-24:])
    tail_values = tuple(series.cells[0].l_series)[len(training) - 24 : len(training)]
    tail = SS.CandidateSeries(
        factor_spec_hash="tail",
        cells=(
            SS.CellSeries(
                cell="L-TD",
                periods=tail_periods,
                l_series=tail_values,
                net_series=tail_values,
            ),
        ),
    )
    assert SS.s1_stat(series, S.W1_CUT) == rank_statistic(tail, S.W1_CUT).value


def test_halves_positive_splits_the_training_window():
    both_positive = S.candidate_series(
        S.shaped_series(first=0.01, second=0.02, test=-0.05)
    )
    assert SS.halves_positive(both_positive, S.W1_CUT) is True

    first_negative = S.candidate_series(
        S.shaped_series(first=-0.06, second=0.10, test=0.05)
    )
    assert SS.halves_positive(first_negative, S.W1_CUT) is False

    second_negative = S.candidate_series(
        S.shaped_series(first=0.06, second=-0.01, test=0.05)
    )
    assert SS.halves_positive(second_negative, S.W1_CUT) is False

    # A W2 run halves 42 -> 21 + 21; the same first-half sign rule applies.
    w2 = S.candidate_series(
        S.shaped_series(first=-0.02, second=0.03, test=0.0, cut=S.W2_CUT),
    )
    assert SS.halves_positive(w2, S.W2_CUT) is False

    # An odd training count is an error, never a silent drop.
    odd_formations = W.month_ends(2015, 12, 55)  # 31 training month-ends
    odd = S.candidate_series(
        S.shaped_series(
            first=0.01,
            second=0.02,
            test=0.0,
            formations=odd_formations,
        ),
        formations=odd_formations,
    )
    with pytest.raises(SS.SelectionStudyError, match="odd period count"):
        SS.halves_positive(odd, S.W1_CUT)


def test_composite_uses_the_argmin_scenario_and_equal_weights():
    strong = S.shaped_series(first=0.02, second=0.03, test=0.0)
    weak = S.shaped_series(first=-0.02, second=-0.03, test=0.0)
    event_cells = {"L-TD-K0": strong, "L-TD-K1": weak}
    first = _ranked("p0", (), cells=event_cells)
    # The argmin scenario of the event candidate is K1 (the weak series).
    assert min(
        (value, cell)
        for cell, value, status, _reason in first.rank_stat.scenarios
        if status == "VALID"
    )[1] == "L-TD-K1"

    series = [
        S.shaped_series(first=0.010, second=0.012, test=0.0),
        S.shaped_series(first=0.009, second=0.011, test=0.0),
        S.shaped_series(first=0.008, second=0.010, test=0.0),
        S.shaped_series(first=0.007, second=0.009, test=0.0),
    ]
    members = [first, *(_ranked(f"p{i + 1}", values) for i, values in enumerate(series))]
    composite = SS.composite_series(members)

    assert [cell for _key, cell in composite.members] == [
        "L-TD-K1",
        "L-TD",
        "L-TD",
        "L-TD",
        "L-TD",
    ]
    expected = []
    for index in range(len(weak)):
        total = 0.0
        for item in [weak, *series]:
            total += item[index]
        expected.append(total / 5.0)
    assert list(composite.values) == expected
    assert composite.periods == S.IS_FORMATIONS

    # A tie between scenarios takes the first in export order (K0 before K1).
    tied = _ranked("p9", (), cells={"L-TD-K0": strong, "L-TD-K1": strong})
    tied_composite = SS.composite_series([tied, *members[1:]])
    assert tied_composite.members[0][1] == "L-TD-K0"

    # Fewer than five members is an error.
    with pytest.raises(SS.SelectionStudyError, match="needs 5"):
        SS.composite_series(members[:4])


# ---------------------------------------------------------------------------
# 3. the rules
# ---------------------------------------------------------------------------


def _pool_of_shapes(cut=S.W1_CUT) -> list[SS.RankedCandidate]:
    ranked = [
        _ranked(f"p{index}", S.pool_series(shape, cut=cut), cut=cut)
        for index, shape in enumerate(S.pool_shapes())
    ]
    return _ordered(ranked, [item.candidate.proposal_id for item in ranked])


@pytest.mark.parametrize("cut,origin", [(S.W1_CUT, "W1"), (S.W2_CUT, "W2")])
def test_rules_pick_known_distinct_answers(cut, origin):
    ranked = _pool_of_shapes(cut)
    selection = {
        rule: SS.select_rule(
            rule, ranked, arm="treatment", origin=origin, window_cut=cut
        )
        for rule in SS.RULES
    }

    # S0 (full-window min EWC t) -> shape a; S1 (last 24) -> shape b;
    # S2 (both halves positive, then S0) -> shape c.
    assert selection["S0"].pick.proposal_id == "p0"  # shape a
    assert selection["S1"].pick.proposal_id == "p1"  # shape b
    assert selection["S2"].pick.proposal_id == "p2"  # shape c
    assert all(item.status == SS.SELECTED for item in selection.values())
    # Three different answers from three different quantities.
    assert len(
        {
            selection["S0"].pick.proposal_id,
            selection["S1"].pick.proposal_id,
            selection["S2"].pick.proposal_id,
        }
    ) == 3
    # S0's pick is exactly the candidate S2's filter removes (negative first
    # half), which is why the two rules cannot agree.
    assert not ranked[0].halves_positive

    # S3 takes the top 5 by S0 (a, c, b, d, e) and averages their argmin series.
    top5 = sorted(ranked, key=lambda item: (-item.s0, item.order))[:5]
    assert [item.candidate.proposal_id for item in top5] == [
        "p0",
        "p2",
        "p1",
        "p3",
        "p4",
    ]
    assert selection["S3"].pick is None
    assert selection["S3"].proposal_ids == ("p0", "p2", "p1", "p3", "p4")
    assert selection["S3"].spec_hashes == tuple(
        item.candidate.factor_spec_hash for item in top5
    )
    assert selection["S3"].training_value == SS.s0_stat(
        SS.composite_series(top5), cut
    )


def test_no_selection_and_tie_breaks():
    # An empty S2 filter is NO_SELECTION, not an error and not a pick.
    ranked = _ordered(
        [
            _ranked("n0", S.shaped_series(first=-0.06, second=0.10, test=0.05)),
            _ranked("n1", S.shaped_series(first=-0.10, second=0.13, test=0.04)),
            _ranked("n2", S.shaped_series(first=0.012, second=-0.004, test=-0.01)),
            _ranked("n3", S.shaped_series(first=-0.010, second=0.011, test=-0.01)),
            _ranked("n4", S.shaped_series(first=0.009, second=-0.010, test=0.0)),
        ],
        ["n0", "n1", "n2", "n3", "n4"],
    )
    empty = SS.select_rule(
        "S2", ranked, arm="control", origin="W1", window_cut=S.W1_CUT
    )
    assert empty.status == SS.NO_SELECTION
    assert empty.pick is None
    assert empty.reason == "empty-filter"
    assert empty.training_value is None

    # Ties go to the earlier proposal, for every rule.
    same = S.shaped_series(first=0.01, second=0.02, test=0.0)
    tied = _ordered(
        [_ranked("t0", same), _ranked("t1", same), _ranked("t2", same)],
        ["t0", "t1", "t2"],
    )
    for rule in ("S0", "S1", "S2"):
        chosen = SS.select_rule(
            rule, tied, arm="control", origin="W1", window_cut=S.W1_CUT
        )
        assert chosen.pick.proposal_id == "t0"
    # ... including the S3 composite's member order.
    five = _ordered(
        [_ranked(f"u{index}", same) for index in range(5)],
        [f"u{index}" for index in range(5)],
    )
    composite = SS.select_rule(
        "S3", five, arm="control", origin="W1", window_cut=S.W1_CUT
    )
    assert composite.proposal_ids == ("u0", "u1", "u2", "u3", "u4")


def test_s3_with_fewer_than_five_candidates_is_an_error():
    ranked = _ordered(_pool_of_shapes()[:4], ["p0", "p1", "p2", "p3"])
    selection = SS.select_rule(
        "S3", ranked, arm="control", origin="W1", window_cut=S.W1_CUT
    )
    assert selection.status == SS.ERROR
    assert selection.pick is None
    assert "fewer-than-5" in (selection.reason or "")
    assert selection.n_survivors == 4


# ---------------------------------------------------------------------------
# 4. the firewall
# ---------------------------------------------------------------------------


def test_every_selection_file_is_written_before_the_first_test_read(env, tmp_path, monkeypatch):
    order: list[str] = []
    real_write = SS.write_selection_file
    real_test = frozen_test_statistics

    def spy_write(path, payload):
        order.append("selection")
        return real_write(path, payload)

    def spy_test(series, cut):
        order.append("test")
        return real_test(series, cut)

    monkeypatch.setattr(SS, "write_selection_file", spy_write)
    monkeypatch.setattr(SS, "test_statistics", spy_test)

    report, exit_code, out = _run_study(env, tmp_path / "study")

    assert exit_code == 0
    assert order.count("selection") == 16
    assert "test" in order
    first_test = order.index("test")
    assert order[:first_test] == ["selection"] * first_test, (
        "a test-window value was read before every selection file was written"
    )
    # Every written selection file exists and matches its recorded hash.
    for run in report["runs"]:
        for rule in SS.RULES:
            selection = run["selections"][rule]
            payload = json.loads((out / selection["file"]).read_text())
            body = {key: value for key, value in payload.items() if key != "selection_sha256"}
            assert payload["selection_sha256"] == SS._canonical_hash(body)
            assert selection["selection_sha256"] == payload["selection_sha256"]


def test_a_leaked_test_period_cannot_reach_a_rule(monkeypatch):
    series = S.candidate_series(
        S.shaped_series(first=0.01, second=0.02, test=0.03)
    )
    # Sanity: the statistic is computable while the window is honoured.
    assert SS.s0_stat(series, S.W1_CUT) == rank_statistic(series, S.W1_CUT).value

    def leaky_window(record, cut, side):
        return tuple(record.periods), tuple(record.l_series)

    monkeypatch.setattr(SS, "window_series", leaky_window)
    for function in (SS.s0_stat, SS.s1_stat, SS.halves_positive):
        with pytest.raises(C4FirewallError):
            function(series, S.W1_CUT)
    # The rule inputs can never be built from a window that leaks the test side.
    with pytest.raises(C4FirewallError):
        _ranked("leak", S.shaped_series(first=0.01, second=0.02, test=0.03))


# ---------------------------------------------------------------------------
# 5. the cache
# ---------------------------------------------------------------------------


def test_second_run_makes_no_evaluator_calls(env, tmp_path):
    env.builder.calls.clear()
    cache = SS.SeriesCache(tmp_path / "cache", evaluate=env.builder)
    _run_study(env, tmp_path / "first", cache=cache)
    assert env.builder.calls, "the first run must evaluate the cache misses"

    env.builder.calls.clear()
    _run_study(env, tmp_path / "second", cache=cache)
    assert env.builder.calls == [], "a warm cache must not re-evaluate anything"


def test_cached_series_reproduces_the_rank_statistic_bitwise(env, tmp_path):
    env.builder.calls.clear()
    env.builder.evaluations.clear()
    cache = SS.SeriesCache(tmp_path / "cache", evaluate=env.builder)
    spec = FactorSpec.from_dict(S.pool_specs("W1")[0])
    key = factor_spec_hash(spec)

    series = cache.get(spec)
    live = env.builder.evaluations[key]

    # A reload from disk only (a fresh cache object, no evaluator).
    reloaded = SS.SeriesCache(tmp_path / "cache").series_for_hash(key)
    assert reloaded == series

    for cut in (S.W1_CUT, S.W2_CUT):
        frozen = rank_statistic(live.td_export, cut)
        assert rank_statistic(reloaded, cut).to_dict() == frozen.to_dict()
        assert SS.s0_stat(reloaded, cut) == frozen.value
        assert frozen_test_statistics(reloaded, cut).to_dict() == frozen_test_statistics(
            live.td_export, cut
        ).to_dict()


def test_report_is_identical_for_jobs_one_and_two(env, tmp_path):
    script = _load_script()
    report_one, code_one, _out_one = _run_study(env, tmp_path / "one")
    assert code_one == 0

    pools = [
        SS.load_run_pool(path)
        for path in sorted(env.run_root.glob("*_*"))
        if (path / "executor_state.json").is_file()
    ]
    specs = [item.factor_spec for pool in pools for item in pool.proposals]
    cache = SS.SeriesCache(tmp_path / "two" / "cache")
    metrics = script.warm_series_cache(
        cache=cache,
        specs=specs,
        builder_factory=lambda: env.builder,
        jobs=2,
    )
    assert metrics["evaluated"] == metrics["requested"]
    assert metrics["requested"] == 12
    assert metrics["workers"] == 2
    report_two, code_two, _out_two = _run_study(env, tmp_path / "two", cache=cache)
    assert code_two == 0

    assert json.dumps(report_one, sort_keys=True) == json.dumps(
        report_two, sort_keys=True
    )


# ---------------------------------------------------------------------------
# 6. the S0 reproduction check
# ---------------------------------------------------------------------------


def test_reproduction_matches_and_mismatches_are_fatal(env, tmp_path):
    report, exit_code, _out = _run_study(env, tmp_path / "study")
    assert exit_code == 0
    assert report["reproduction"]["status"] == "NOT_CHECKED"

    matching = S.fake_m1e_report(report)
    good_dir = tmp_path / "m1e_good"
    good_dir.mkdir()
    (good_dir / "c4_walkforward_report.json").write_text(
        json.dumps(matching), encoding="utf-8"
    )
    reproduced, code, _out = _run_study(
        env, tmp_path / "reproduced", m1e_reports=[good_dir]
    )
    assert code == 0
    assert reproduced["reproduction"]["status"] == "REPRODUCED"
    assert reproduced["reproduction"]["n_failed"] == 0
    assert reproduced["reproduction"]["n_checks"] > 0

    # One wrong training value must fail the run (non-zero exit).
    mutated = copy.deepcopy(matching)
    mutated["runs"][0]["selection"]["rank_stat"] += 1e-6
    bad_dir = tmp_path / "m1e_bad"
    bad_dir.mkdir()
    (bad_dir / "c4_walkforward_report.json").write_text(
        json.dumps(mutated), encoding="utf-8"
    )
    failed, code, out = _run_study(env, tmp_path / "failed", m1e_reports=[bad_dir])
    assert code != 0
    assert failed["reproduction"]["status"] == "REPRODUCTION_FAILED"
    assert failed["reproduction"]["n_failed"] == 1
    written = json.loads((out / "c4_selection_study_report.json").read_text())
    assert written["reproduction"]["status"] == "REPRODUCTION_FAILED"

    # A wrong EVALUATED count is fatal too (the draft's 50/41/62/73 hard check).
    wrong_count = copy.deepcopy(matching)
    wrong_count["runs"][0]["counts"]["EVALUATED"] += 1
    count_dir = tmp_path / "m1e_count"
    count_dir.mkdir()
    (count_dir / "c4_walkforward_report.json").write_text(
        json.dumps(wrong_count), encoding="utf-8"
    )
    counted, code, _out = _run_study(env, tmp_path / "counted", m1e_reports=[count_dir])
    assert code != 0
    assert counted["reproduction"]["status"] == "REPRODUCTION_FAILED"


# ---------------------------------------------------------------------------
# 7. safety + the pre-declared reading
# ---------------------------------------------------------------------------


def test_the_study_reports_the_frozen_reading(env, tmp_path):
    report, exit_code, _out = _run_study(env, tmp_path / "study")
    assert exit_code == 0

    assert report["campaign"] == "campaign4-m1s-selection-study"
    assert [run["arm"] for run in report["runs"]] == [
        "control",
        "control",
        "treatment",
        "treatment",
    ]
    assert report["trial_accounting"]["selection_trials"] == 16
    assert report["trial_accounting"]["m1e_proposal_trials"] == 400

    # W1 rows carry an EWC t; W2 rows are descriptive only, n = 12.
    for run in report["runs"]:
        for rule in SS.RULES:
            test = run["tests"][rule]
            assert test is not None
            if run["origin"] == "W1":
                assert test["test_statistic"] == "ewc_t"
                assert test["ewc_t"] is not None
                assert test["n"] == 24
                assert test["descriptive_only"] is False
            else:
                assert test["test_statistic"] == "mean"
                assert test["ewc_t"] is None
                assert test["n"] == 12
                assert test["descriptive_only"] is True
        rate = run["base_rate"]
        assert rate["n"] == rate["n_pool"] == 6
        assert rate["statistic"] == ("ewc_t" if run["origin"] == "W1" else "mean")
        assert rate["spearman"] is not None

    # PROMISING needs a positive W1 test t and a positive W2 test mean in both
    # arms: S0/S1/S3 pick the positive-test shapes, S2 picks a negative one.
    assert report["flags"]["promising"] == {
        "S0": True,
        "S1": True,
        "S2": False,
        "S3": True,
    }
    assert report["flags"]["pool_limited"] is False


def test_pool_limited_flag_reads_the_frozen_condition():
    def run(name, median, spearman):
        return {
            "run": name,
            "base_rate": {"median": median, "spearman": spearman},
        }

    negative = [
        run("treatment_W1", -0.5, -0.4),
        run("treatment_W2", -0.2, -0.1),
        run("control_W1", -0.3, 0.0),
        run("control_W2", 0.0, -0.2),
    ]
    assert SS._flag_pool_limited(negative)["pool_limited"] is True

    positive = [
        run("treatment_W1", 0.5, -0.4),
        run("treatment_W2", -0.2, -0.1),
        run("control_W1", -0.3, 0.0),
        run("control_W2", 0.0, -0.2),
    ]
    assert SS._flag_pool_limited(positive)["pool_limited"] is False

    undefined = [
        run("treatment_W1", -0.5, None),
        run("treatment_W2", -0.2, -0.1),
        run("control_W1", -0.3, 0.0),
        run("control_W2", 0.0, -0.2),
    ]
    assert SS._flag_pool_limited(undefined)["pool_limited"] is False


def test_no_kpit_write_and_the_m1e_run_root_is_untouched(env, tmp_path, monkeypatch):
    before = _hash_tree(env.run_root)
    kpit = tmp_path / "kpit"
    kpit.mkdir()
    (kpit / "knowledge.json").write_text('{"entries": []}\n', encoding="utf-8")
    kpit_before = _hash_tree(kpit)

    writes: list[Path] = []
    real_write_text = Path.write_text

    def spy_write_text(self, *args, **kwargs):
        writes.append(Path(self).resolve())
        return real_write_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", spy_write_text)
    _report, exit_code, out = _run_study(env, tmp_path / "study")
    monkeypatch.undo()

    assert exit_code == 0
    resolved_kpit = kpit.resolve()
    resolved_run_root = env.run_root.resolve()
    resolved_out = out.resolve()
    resolved_cache = (tmp_path / "study" / "cache").resolve()
    assert writes, "the study must write its artifacts"
    for path in writes:
        assert resolved_kpit not in path.parents
        assert path != resolved_kpit / "knowledge.json"
        assert resolved_run_root not in path.parents
        assert resolved_out in path.parents or resolved_cache in path.parents
    assert _hash_tree(kpit) == kpit_before
    assert _hash_tree(env.run_root) == before


def test_refuses_in_repository_and_data_store_paths(env, tmp_path):
    with pytest.raises(SS.SelectionStudyError, match="inside the repository"):
        SS.assert_outside_repository(SS.REPO_ROOT / "scratch_out")
    with pytest.raises(SS.SelectionStudyError):
        SS.assert_outside_repository(SS.REPO_ROOT / "data_store" / "c4m1s")
    with pytest.raises(SS.SelectionStudyError, match="read-only"):
        SS.assert_not_inside(
            tmp_path / "runs" / "inside", tmp_path / "runs", what="the output directory"
        )
    # A report directory inside the M1-e run root is refused by the study too.
    with pytest.raises(SS.SelectionStudyError, match="read-only"):
        _run_study(env, env.run_root / "report")

    # ``main`` refuses an in-repository path before any dataset access.
    script = _load_script()
    with pytest.raises(SS.SelectionStudyError, match="inside the repository"):
        script.main(
            [
                "--dataset-root",
                "/nonexistent-dataset",
                "--knowledge-log",
                "/nonexistent-kpit",
                "--m1e-run-root",
                str(env.run_root),
                "--series-cache",
                str(SS.REPO_ROOT / "cache_should_not_be_written"),
                "--output-dir",
                str(tmp_path / "out"),
            ]
        )
