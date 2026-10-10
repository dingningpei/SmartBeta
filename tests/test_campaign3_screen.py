"""C3-S -- IS ranking, TB_SELECTION write-ahead, T-B floor, forwarding tests.

The whole suite is synthetic and disposable: in-memory P-Q diagnostics, a stub
T-B evaluator, a recording TB_SELECTION writer and the admitted
``EWC_T23_ONE_SIDED`` procedure.  An autouse guard refuses any
``data_store`` / ``reserve_sealed`` path and any socket, and a credential
sentinel proves no credential value leaks into the report.
"""

from __future__ import annotations

import builtins
import importlib.util
import json
import pathlib
import socket
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from smart_beta.campaign import campaign3_screen as SCREEN
from smart_beta.campaign.campaign3_screen import (
    BreakEvenUnavailable,
    GrossNet,
    RankingTable,
    ScreeningCandidate,
    TbFirewallError,
    TbSelection,
    TbHypothesisResult,
    break_even_cost_bps,
    evaluate_tb,
    forward_decision,
    rank_candidates,
    render_markdown,
    run_screen,
    write_tb_selection,
)
from smart_beta.science.inference import InferenceStatus

_REPO_ROOT = Path(__file__).resolve().parents[1]
_FIXTURE_PATH = (
    _REPO_ROOT / "tests" / "fixtures" / "campaign3_screen" / "synthetic.py"
)
_MUTATIONS_PATH = (
    _REPO_ROOT / "tests" / "fixtures" / "campaign3_screen" / "mutations.json"
)
CREDENTIAL_SENTINEL = "c3s-credential-sentinel-7d2a"


def _load_synthetic():
    name = "c3s_screen_synthetic"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _FIXTURE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


S = _load_synthetic()


# ---------------------------------------------------------------------------
# guards
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_sockets(monkeypatch: pytest.MonkeyPatch):
    def _refuse(*args: object, **kwargs: object) -> None:
        raise RuntimeError("C3-S tests must never open a socket")

    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(socket, "create_connection", _refuse)
    yield


@pytest.fixture(autouse=True)
def _forbid_data_paths(monkeypatch: pytest.MonkeyPatch):
    real_open = builtins.open
    real_path_open = pathlib.Path.open

    def _check(target: object) -> None:
        text = str(target)
        if "reserve_sealed" in text or "data_store" in text:
            raise AssertionError(f"C3-S must never open {text!r}")

    def _guard(target, *args, **kwargs):
        _check(target)
        return real_open(target, *args, **kwargs)

    def _path_guard(self, *args, **kwargs):
        _check(self)
        return real_path_open(self, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", _guard)
    monkeypatch.setattr(pathlib.Path, "open", _path_guard)
    yield


@pytest.fixture(autouse=True)
def _credential_sentinel(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TIINGO_API_KEY", CREDENTIAL_SENTINEL)
    yield


def _registry():
    return S.registry()


def _admission():
    return [S.admission_record()]


def _rank(candidates):
    return rank_candidates(candidates, registry=_registry(), admission_records=_admission())


def _values(b, l, event_scenarios=()):
    return S._uniform_values(b=b, l=l, event_scenarios=event_scenarios)


# ---------------------------------------------------------------------------
# D9 ranking
# ---------------------------------------------------------------------------


def test_ranking_top_k_and_order() -> None:
    candidates = [
        S.candidate(
            f"H{i}",
            values=_values(
                b=S.series(0.05, n=S.N_TB),
                l=S.series(0.05 - 0.004 * i, n=S.N_TB),
            ),
        )
        for i in range(7)
    ]
    table = _rank(candidates)
    assert isinstance(table, RankingTable)
    assert table.selected == ("H0", "H1", "H2", "H3", "H4")
    eligible = [row for row in table.rows if row.eligible]
    assert [row.selected for row in eligible] == [True] * 5 + [False] * 2


def test_ranking_uses_l_envelope_not_b() -> None:
    a = S.candidate(
        "H_A",
        values=_values(b=S.series(0.05, n=S.N_TB), l=S.series(0.002, n=S.N_TB)),
    )
    b = S.candidate(
        "H_B",
        values=_values(b=S.series(0.002, n=S.N_TB), l=S.series(0.04, n=S.N_TB)),
    )
    table = _rank([a, b])
    assert table.selected[0] == "H_B"


def test_ranking_uses_minimum_not_maximum() -> None:
    a = S.candidate(
        "H_A",
        values={
            "B-T0": S.series(0.05, n=S.N_TB),
            "B-TS": S.series(0.05, n=S.N_TB),
            "L-T0": S.series(0.05, n=S.N_TB),
            "L-TS": S.series(-0.05, n=S.N_TB),
        },
    )
    b = S.candidate(
        "H_B",
        values={
            "B-T0": S.series(0.05, n=S.N_TB),
            "B-TS": S.series(0.05, n=S.N_TB),
            "L-T0": S.series(0.02, n=S.N_TB),
            "L-TS": S.series(0.02, n=S.N_TB),
        },
    )
    table = _rank([a, b])
    assert table.selected[0] == "H_B"


def test_ranking_applies_declared_sign() -> None:
    candidate = S.candidate(
        "H_NEG",
        sign=-1,
        values=_values(b=S.series(0.05, n=S.N_TB), l=S.series(-0.05, n=S.N_TB)),
    )
    table = _rank([candidate])
    row = table.rows[0]
    assert row.eligible
    assert row.min_t is not None and row.min_t > 0.0


def test_ranking_event_four_scenarios() -> None:
    candidate = S.candidate(
        "H_EVENT",
        event_scenarios=("K0", "K1"),
        values={
            "B-T0-K0": S.series(0.05, n=S.N_TB),
            "B-TS-K0": S.series(0.05, n=S.N_TB),
            "B-T0-K1": S.series(0.05, n=S.N_TB),
            "B-TS-K1": S.series(0.05, n=S.N_TB),
            "L-T0-K0": S.series(0.05, n=S.N_TB),
            "L-TS-K0": S.series(0.04, n=S.N_TB),
            "L-T0-K1": S.series(0.03, n=S.N_TB),
            "L-TS-K1": S.series(0.02, n=S.N_TB),
        },
    )
    table = _rank([candidate])
    row = table.rows[0]
    assert row.eligible
    assert [item.scenario for item in row.scenarios] == [
        "T0-K0",
        "TS-K0",
        "T0-K1",
        "TS-K1",
    ]
    assert row.min_t == row.scenarios[-1].statistic


def test_ranking_non_event_k0_equals_k1() -> None:
    candidate = S.candidate(
        "H_SIMPLE",
        values=_values(b=S.series(0.05, n=S.N_TB), l=S.series(0.03, n=S.N_TB)),
    )
    table = _rank([candidate])
    row = table.rows[0]
    assert len(row.scenarios) == 4
    by_scenario = {item.scenario: item for item in row.scenarios}
    assert by_scenario["T0-K0"].cell == by_scenario["T0-K1"].cell == "L-T0"
    assert by_scenario["T0-K0"].statistic == by_scenario["T0-K1"].statistic
    assert by_scenario["TS-K0"].statistic == by_scenario["TS-K1"].statistic


def test_ranking_ties_broken_by_canonical_key() -> None:
    values = _values(b=S.series(0.05, n=S.N_TB), l=S.series(0.03, n=S.N_TB))
    b = S.candidate("H_B", canonical_key="bbb", values=values)
    a = S.candidate("H_A", canonical_key="aaa", values=values)
    table = _rank([b, a])
    assert table.selected == ("H_A", "H_B")


def test_ranking_excludes_invalid_and_not_assessed() -> None:
    good = S.candidate(
        "H_GOOD", values=_values(b=S.series(0.05, n=S.N_TB), l=S.series(0.03, n=S.N_TB))
    )
    missing = ScreeningCandidate(
        "H_MISSING",
        "H_MISSING",
        1,
        S.make_diagnostics(
            hypothesis_id="H_MISSING",
            values=_values(b=S.series(0.05, n=S.N_TB), l=S.series(0.03, n=S.N_TB)),
        ),
    )
    # Remove every L cell so the scenario is missing.
    missing = ScreeningCandidate(
        "H_MISSING",
        "H_MISSING",
        1,
        _without_cells(missing.diagnostics, ("L-T0", "L-TS")),
    )
    not_assessed = ScreeningCandidate(
        "H_NA",
        "H_NA",
        1,
        S.make_diagnostics(
            hypothesis_id="H_NA",
            values=_values(b=S.series(0.05, n=S.N_TB), l=S.series(0.03, n=S.N_TB)),
            certified=False,
            not_assessed="PQV2_NOT_ASSESSED",
        ),
    )
    table = _rank([good, missing, not_assessed])
    by_id = {row.hypothesis_id: row for row in table.rows}
    assert by_id["H_GOOD"].eligible
    assert not by_id["H_MISSING"].eligible
    assert not by_id["H_NA"].eligible
    assert table.selected == ("H_GOOD",)


def _without_cells(diagnostics, cells):
    kept = tuple(cell for cell in diagnostics.cells if cell.cell not in cells)
    return diagnostics.__class__(**{**diagnostics.__dict__, "cells": kept})


# ---------------------------------------------------------------------------
# TB_SELECTION write-ahead ordering (S-M4)
# ---------------------------------------------------------------------------


def _selection(*ids, control="event_ear_k_kp1"):
    return TbSelection(
        selected_ids=tuple(ids),
        control_id=control,
        ranking_table_hash="f" * 64,
    )


def _tb_results(ids, *, pass_all=True):
    return {
        hid: S.make_tb(hid, b=S.STRONG_POS, l=S.STRONG_POS)
        for hid in ids
    }


def test_tb_selection_written_before_evaluation() -> None:
    events: list[tuple] = []
    writer = S.RecordingSelectionWriter(events=events)

    def _on_eval(hypothesis_id: str) -> None:
        events.append(("evaluate", hypothesis_id))

    ids = ("H0", "H1", "H2", "H3", "H4")
    evaluator = S.StubTbEvaluator(
        results=_tb_results(ids + ("event_ear_k_kp1",)), on_call=_on_eval
    )
    selection = _selection(*ids)
    write_tb_selection(writer, selection)
    sign_by_id = {hid: 1 for hid in ids + ("event_ear_k_kp1",)}
    evaluate_tb(
        selection=selection,
        sign_by_id=sign_by_id,
        control_id="event_ear_k_kp1",
        tb_evaluator=evaluator,
        registry=_registry(),
        admission_records=_admission(),
    )
    assert events[0][0] == "write"
    assert all(event[0] == "evaluate" for event in events[1:])
    assert [event[1] for event in events[1:]] == list(ids) + ["event_ear_k_kp1"]


def test_tb_evaluation_requires_selection_write() -> None:
    ids = ("H0",)
    evaluator = S.StubTbEvaluator(
        results=_tb_results(ids + ("event_ear_k_kp1",))
    )
    selection = _selection(*ids)
    assert not selection.written
    with pytest.raises(TbFirewallError):
        evaluate_tb(
            selection=selection,
            sign_by_id={hid: 1 for hid in ids + ("event_ear_k_kp1",)},
            control_id="event_ear_k_kp1",
            tb_evaluator=evaluator,
            registry=_registry(),
            admission_records=_admission(),
        )
    assert evaluator.calls == []


def test_tb_selection_writer_failure_blocks_evaluation(tmp_path: Path) -> None:
    ids = ("H0", "H1", "H2", "H3", "H4")
    events: list[tuple] = []
    writer = S.RecordingSelectionWriter(events=events, fail=True)
    control = S.candidate("event_ear_k_kp1", values=_values(
        b=S.STRONG_POS, l=S.STRONG_POS
    ))
    evaluator = S.StubTbEvaluator(results=_tb_results(ids + ("event_ear_k_kp1",)))
    candidates = [
        S.candidate(hid, values=_values(b=S.series(0.05, n=S.N_TB), l=S.series(0.05 - 0.004 * i, n=S.N_TB)))
        for i, hid in enumerate(ids)
    ]
    with pytest.raises(RuntimeError):
        run_screen(
            candidates=candidates,
            control=control,
            registry=_registry(),
            admission_records=_admission(),
            selection_writer=writer,
            tb_evaluator=evaluator,
        )
    assert evaluator.calls == []


# ---------------------------------------------------------------------------
# T-B firewall (S-M5)
# ---------------------------------------------------------------------------


def test_tb_firewall_rejects_seventh_hypothesis() -> None:
    ids = ("H0", "H1", "H2", "H3", "H4")
    results = _tb_results(ids + ("event_ear_k_kp1", "H7"))
    evaluator = S.StubTbEvaluator(results=results)
    selection = _selection(*ids)
    write_tb_selection(S.RecordingSelectionWriter(), selection)
    sign_by_id = {hid: 1 for hid in ids + ("event_ear_k_kp1", "H7")}
    evaluate_tb(
        selection=selection,
        sign_by_id=sign_by_id,
        control_id="event_ear_k_kp1",
        tb_evaluator=evaluator,
        registry=_registry(),
        admission_records=_admission(),
    )
    assert evaluator.calls == list(ids) + ["event_ear_k_kp1"]
    assert "H7" not in evaluator.calls


def test_tb_firewall_rejects_mismatched_diagnostics() -> None:
    ids = ("H0",)
    wrong = SCREEN.TbDiagnostics(
        hypothesis_id="H_OTHER",
        diagnostics=S.make_diagnostics(hypothesis_id="H_OTHER"),
        gross_net={"T0": GrossNet(0.05, 0.04)},
    )
    evaluator = S.StubTbEvaluator(results={"H0": wrong, "event_ear_k_kp1": wrong})
    selection = _selection(*ids)
    write_tb_selection(S.RecordingSelectionWriter(), selection)
    with pytest.raises(TbFirewallError):
        evaluate_tb(
            selection=selection,
            sign_by_id={"H0": 1, "event_ear_k_kp1": 1},
            control_id="event_ear_k_kp1",
            tb_evaluator=evaluator,
            registry=_registry(),
            admission_records=_admission(),
        )


# ---------------------------------------------------------------------------
# D8 floor
# ---------------------------------------------------------------------------


def _evaluate(tb_map, *, sign_by_id, control="event_ear_k_kp1"):
    tb_map = dict(tb_map)
    if control not in tb_map:
        tb_map[control] = S.make_tb(control, b=S.STRONG_POS, l=S.STRONG_POS)
    sign_by_id = dict(sign_by_id)
    sign_by_id.setdefault(control, 1)
    ids = tuple(tb_map)
    selection = _selection(*[hid for hid in ids if hid != control], control=control)
    write_tb_selection(S.RecordingSelectionWriter(), selection)
    evaluator = S.StubTbEvaluator(results=tb_map)
    return evaluate_tb(
        selection=selection,
        sign_by_id=sign_by_id,
        control_id=control,
        tb_evaluator=evaluator,
        registry=_registry(),
        admission_records=_admission(),
    )


def test_floor_requires_b_and_l_all_scenarios() -> None:
    tb = S.make_tb("H0", b=S.STRONG_POS, l=S.STRONG_NEG)
    results = _evaluate({"H0": tb}, sign_by_id={"H0": 1})
    assert results[0].floor_pass is False
    # B alone passes; the L failure is what sinks the floor.
    b_scenarios = [item for item in results[0].scenarios if item.family == "B"]
    l_scenarios = [item for item in results[0].scenarios if item.family == "L"]
    assert all(item.passed for item in b_scenarios)
    assert not any(item.passed for item in l_scenarios)


def test_floor_event_requires_all_four_scenarios() -> None:
    tb = S.make_tb(
        "H_EVENT",
        event_scenarios=("K0", "K1"),
        b=S.STRONG_POS,
        l=S.STRONG_POS,
    )
    # One K1 scenario fails.
    tb = _replace_tb_cell(tb, "L-TS-K1", S.STRONG_NEG)
    results = _evaluate({"H_EVENT": tb}, sign_by_id={"H_EVENT": 1})
    assert results[0].floor_pass is False


def _replace_tb_cell(tb, cell, values):
    diagnostics = tb.diagnostics
    cells = tuple(
        (
            item.__class__(**{**item.__dict__, "period_values": tuple(float(v) for v in values)})
            if item.cell == cell
            else item
        )
        for item in diagnostics.cells
    )
    new_diag = diagnostics.__class__(**{**diagnostics.__dict__, "cells": cells})
    return SCREEN.TbDiagnostics(
        hypothesis_id=tb.hypothesis_id,
        diagnostics=new_diag,
        gross_net=tb.gross_net,
    )


def test_floor_is_unadjusted_not_holm() -> None:
    fake = SimpleNamespace(status=InferenceStatus.VALID, p_one_sided=0.03)
    # 0.03 <= 0.05 (unadjusted) but 0.03 > 0.05/8 (Holm over 8 cells).
    assert SCREEN._floor_significant(fake, SCREEN.FLOOR_ALPHA) is True


# ---------------------------------------------------------------------------
# D10 forwarding + D11 control (S-M7)
# ---------------------------------------------------------------------------


def _tb_result(hypothesis_id, min_l_t, *, pass_floor=True, break_even=None):
    return TbHypothesisResult(
        hypothesis_id=hypothesis_id,
        sign=1,
        scenarios=(),
        floor_pass=pass_floor,
        min_l_t=min_l_t,
        break_even_by_scenario={},
        break_even_cost_bps=break_even,
        is_control=False,
    )


def test_forward_largest_min_l_t() -> None:
    results = [
        _tb_result("H0", 5.0),
        _tb_result("H1", 9.0),
        _tb_result("H2", 7.0),
        _tb_result("event_ear_k_kp1", 100.0),
    ]
    decision = forward_decision(results, control_id="event_ear_k_kp1")
    assert decision.decision == "FORWARD"
    assert decision.hypothesis_id == "H1"
    assert decision.min_l_t == 9.0


def test_control_never_forwarded() -> None:
    results = [
        _tb_result("event_ear_k_kp1", 100.0),
        _tb_result("H0", 3.0),
    ]
    decision = forward_decision(results, control_id="event_ear_k_kp1")
    assert decision.decision == "FORWARD"
    assert decision.hypothesis_id == "H0"


def test_no_reserve_use_when_none_pass() -> None:
    results = [
        _tb_result("H0", 5.0, pass_floor=False),
        _tb_result("event_ear_k_kp1", 100.0, pass_floor=True),
    ]
    decision = forward_decision(results, control_id="event_ear_k_kp1")
    assert decision.decision == "NO_RESERVE_USE"
    assert decision.hypothesis_id is None


# ---------------------------------------------------------------------------
# D12 break-even (S-M9)
# ---------------------------------------------------------------------------


def test_break_even_formula() -> None:
    assert break_even_cost_bps(gross_mean=0.05, net_mean=0.04) == pytest.approx(50.0)
    assert break_even_cost_bps(gross_mean=0.02, net_mean=0.02) is None


def test_break_even_is_not_a_gate() -> None:
    results = [
        _tb_result("H_TINY", 9.0, break_even=0.5),
        _tb_result("H_BIG", 7.0, break_even=500.0),
    ]
    decision = forward_decision(results, control_id="event_ear_k_kp1")
    # The largest min L t wins; the tiny break-even never gates it out.
    assert decision.hypothesis_id == "H_TINY"


def test_break_even_unavailable_names_available_fields() -> None:
    tb = S.make_tb("H0", with_break_even=False)
    selection = _selection("H0")
    write_tb_selection(S.RecordingSelectionWriter(), selection)
    evaluator = S.StubTbEvaluator(results={"H0": tb})
    with pytest.raises(BreakEvenUnavailable) as excinfo:
        evaluate_tb(
            selection=selection,
            sign_by_id={"H0": 1},
            control_id="event_ear_k_kp1",
            tb_evaluator=evaluator,
            registry=_registry(),
            admission_records=_admission(),
        )
    message = str(excinfo.value)
    assert "available fields" in message
    assert "certified_hypothesis" in message


# ---------------------------------------------------------------------------
# end-to-end run + report
# ---------------------------------------------------------------------------


def _run_end_to_end(tmp_path: Path):
    ids = ("H0", "H1", "H2", "H3", "H4")
    candidates = [
        S.candidate(
            hid,
            values=_values(
                b=S.series(0.05, n=S.N_TB),
                l=S.series(0.05 - 0.004 * i, n=S.N_TB),
            ),
        )
        for i, hid in enumerate(ids)
    ]
    candidates.append(
        S.candidate("H5", values=_values(b=S.series(-0.05, n=S.N_TB), l=S.series(-0.05, n=S.N_TB)))
    )
    control = S.candidate(
        "event_ear_k_kp1",
        values=_values(b=S.series(0.05, n=S.N_TB), l=S.series(0.05, n=S.N_TB)),
    )
    tb_map = {
        hid: S.make_tb(hid, b=S.STRONG_POS, l=S.series(0.05 - 0.002 * i, n=S.N_TB))
        for i, hid in enumerate(ids)
    }
    tb_map["event_ear_k_kp1"] = S.make_tb(
        "event_ear_k_kp1", b=S.STRONG_POS, l=S.series(0.06, n=S.N_TB)
    )
    evaluator = S.StubTbEvaluator(results=tb_map)
    writer = S.RecordingSelectionWriter()
    report = run_screen(
        candidates=candidates,
        control=control,
        registry=_registry(),
        admission_records=_admission(),
        selection_writer=writer,
        tb_evaluator=evaluator,
        report_dir=tmp_path,
    )
    return report, evaluator, writer


def test_run_screen_end_to_end(tmp_path: Path) -> None:
    report, evaluator, writer = _run_end_to_end(tmp_path)
    assert report.ranking.selected == ("H0", "H1", "H2", "H3", "H4")
    assert evaluator.calls == [
        "H0",
        "H1",
        "H2",
        "H3",
        "H4",
        "event_ear_k_kp1",
    ]
    assert report.selection.written is True
    assert report.selection.record_hash is not None
    # H0 has the largest T-B min L t among floor passers.
    assert report.forwarding.decision == "FORWARD"
    assert report.forwarding.hypothesis_id == "H0"
    assert report.control_verdict == "PIPELINE_OK"


def test_run_screen_no_reserve_use(tmp_path: Path) -> None:
    ids = ("H0", "H1", "H2", "H3", "H4")
    candidates = [
        S.candidate(
            hid,
            values=_values(b=S.series(0.05, n=S.N_TB), l=S.series(0.05 - 0.004 * i, n=S.N_TB)),
        )
        for i, hid in enumerate(ids)
    ]
    control = S.candidate(
        "event_ear_k_kp1",
        values=_values(b=S.STRONG_POS, l=S.STRONG_POS),
    )
    tb_map = {
        hid: S.make_tb(hid, b=S.STRONG_NEG, l=S.STRONG_NEG) for hid in ids
    }
    tb_map["event_ear_k_kp1"] = S.make_tb(
        "event_ear_k_kp1", b=S.STRONG_POS, l=S.STRONG_POS
    )
    report = run_screen(
        candidates=candidates,
        control=control,
        registry=_registry(),
        admission_records=_admission(),
        selection_writer=S.RecordingSelectionWriter(),
        tb_evaluator=S.StubTbEvaluator(results=tb_map),
        report_dir=tmp_path,
    )
    assert report.forwarding.decision == "NO_RESERVE_USE"


def test_report_json_power_table_verbatim(tmp_path: Path) -> None:
    _run_end_to_end(tmp_path)
    payload = json.loads(
        (tmp_path / "campaign3_screen_report.json").read_text(encoding="utf-8")
    )
    assert payload["tb_label"] == "DESCRIPTIVE, NOT CLAIMS"
    assert payload["p5_power_table_markdown"] == SCREEN.P5_POWER_TABLE_MARKDOWN
    assert payload["break_even_label"] == "ASSUMED"
    assert payload["forwarding"]["decision"] == "FORWARD"


def test_report_markdown_labels_tb(tmp_path: Path) -> None:
    _run_end_to_end(tmp_path)
    markdown = (tmp_path / "campaign3_screen_report.md").read_text(encoding="utf-8")
    assert "DESCRIPTIVE, NOT CLAIMS" in markdown
    assert SCREEN.P5_POWER_TABLE_MARKDOWN in markdown
    assert "ASSUMED" in markdown


def test_credential_sentinel_never_leaks(tmp_path: Path) -> None:
    report, _, _ = _run_end_to_end(tmp_path)
    blob = json.dumps(report.to_dict()) + render_markdown(report)
    assert CREDENTIAL_SENTINEL not in blob


# ---------------------------------------------------------------------------
# mutation manifest contract
# ---------------------------------------------------------------------------


def test_mutation_fixture_contract() -> None:
    mutations = json.loads(_MUTATIONS_PATH.read_text(encoding="utf-8"))
    assert [m["id"] for m in mutations] == [f"S-M{i}" for i in range(1, 10)]
    for mutation in mutations:
        assert mutation["edits"], mutation["id"]
        assert mutation["file"] == "smart_beta/campaign/campaign3_screen.py"
        source = (_REPO_ROOT / mutation["file"]).read_text(encoding="utf-8")
        for edit in mutation["edits"]:
            assert edit["old"] and edit["new"] is not None, mutation["id"]
            assert source.count(edit["old"]) == 1, mutation["id"]
            assert edit["old"] != edit["new"], mutation["id"]
        module_name, _, test_name = mutation["test"].partition("::")
        assert test_name, mutation["id"]
        module_source = (_REPO_ROOT / module_name).read_text(encoding="utf-8")
        assert f"def {test_name.split('[')[0]}" in module_source, mutation["id"]
