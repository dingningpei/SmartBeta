"""PQ-SX — STD_X_RANK ``dv`` observed-value alignment against the D7c bridge.

The frozen methodology is unchanged (P-Q v2.2 amendment Props. 5-6: ``V`` is a
ratio of rolling ``dv`` means, both windows ending at ``f - 1``).  This suite
locks the **implementation** alignment: the contract §11 bridge makes the D7c
frame rows already lagged one session, so the observation of session ``f + u``
is carried by row ``f + u + 1`` -- not by the raw session ``f + u``.  Reading
the raw session double-lags ``dv`` (through ``f - 2``) and makes the
reconstructed V box disagree with the realization at row ``f``.

Everything is offline and synthetic: an autouse socket guard and a credential
sentinel mirror the other P-Q v2 suites.  No production artifact, reserve file,
holdout, ``raw/``, ``dsc_evidence`` or Knowledge-PIT path is read.  The V box
oracle (``tests/fixtures/pqv2/stdxrank_alignment/oracle.py``) has no
``smart_beta`` imports and is the independent reference for the alignment.
"""

from __future__ import annotations

import importlib.util
import json
import math
import socket
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd
import pytest

from smart_beta.dataset_v1 import pqv2_assign_sandwich as SW
from smart_beta.dataset_v1 import pqv2_core as C
from smart_beta.dataset_v1 import pqv2_dependency as D
from smart_beta.dataset_v1 import pqv2_evaluation as E

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = (
    REPO_ROOT
    / "tests"
    / "fixtures"
    / "pqv2"
    / "stdxrank_alignment"
    / "fixture.py"
)
MUTATIONS = (
    REPO_ROOT
    / "tests"
    / "fixtures"
    / "pqv2"
    / "stdxrank_alignment"
    / "mutations.json"
)
CREDENTIAL_SENTINEL = "pqsx-credential-sentinel-7b41"
_CREDENTIAL_KEYS = (
    "TIINGO_API_KEY",
    "TUSHARE_PROXY_TOKEN",
    "TUSHARE_BASIC_PROXY_TOKEN",
    "TUSHARE_API_TOKEN",
    "DEEPSEEK_API_KEY",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "OPENAI_API_KEY",
)

#: ULP-level tolerance: the production box sums ``dv`` sequentially while the
#: evaluator and the oracle use different (but equivalent) reductions, so a
#: bit-identical value is not guaranteed.  The observed spread is < 1e-11
#: relative; the defect signal is ~1e-1, so this tolerance still isolates the
#: alignment (and its mutations).
_REL = 1e-9
_ABS = 1e-12


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_sockets(monkeypatch: pytest.MonkeyPatch):
    def _refuse(*args: object, **kwargs: object) -> None:
        raise RuntimeError("PQ-SX tests must never open a socket")

    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(socket, "create_connection", _refuse)
    yield


@pytest.fixture(autouse=True)
def _credential_sentinel(monkeypatch: pytest.MonkeyPatch):
    for key in _CREDENTIAL_KEYS:
        monkeypatch.setenv(key, CREDENTIAL_SENTINEL)
    yield


def test_credential_sentinel_never_leaks() -> None:
    assert E.PQ_METHODOLOGY != CREDENTIAL_SENTINEL


# ---------------------------------------------------------------------------
# Fixture loading
# ---------------------------------------------------------------------------


def _load_alignment():
    name = "pqsx_alignment_fixture"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, FIXTURE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def align():
    return _load_alignment()


@pytest.fixture(scope="module")
def oracle(align):
    return align.oracle


def test_control_is_green(oracle) -> None:
    """No-op control: the unmutated oracle and production module import."""

    # series [1..6], formation row 5: short (4+5)/2, long (1..5)/5.
    value = oracle.v_ratio([1.0, 2.0, 3.0, 4.0, 5.0, 6.0], 5, 2, 5)
    assert value == pytest.approx(1.5)
    assert E._observed_values.__module__.startswith("smart_beta")


def test_oracle_module_has_no_smart_beta_import(oracle) -> None:
    """The independent oracle must never import the production code."""

    import ast

    path = (
        REPO_ROOT
        / "tests"
        / "fixtures"
        / "pqv2"
        / "stdxrank_alignment"
        / "oracle.py"
    )
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith("smart_beta")
        elif isinstance(node, ast.ImportFrom):
            assert not (node.module or "").startswith("smart_beta")


# ---------------------------------------------------------------------------
# Unit: the session -> row alignment of one observed dv value
# ---------------------------------------------------------------------------


def _small_frame(observations: Sequence[float]) -> pd.DataFrame:
    sessions = [date(2020, 1, 1) + timedelta(days=index) for index in range(len(observations))]
    frame = pd.DataFrame(index=pd.to_datetime(sessions), columns=["A"], dtype="float64")
    row = 0
    for session, value in zip(sessions, observations):
        # prev(d): row r carries the observation of r - 1.
        frame.at[pd.Timestamp(session), "A"] = (
            observations[row - 1] if row > 0 else observations[0]
        )
        row += 1
    return frame


def test_observed_values_reads_the_bridge_row(oracle) -> None:
    """The observation of session ``f + u`` comes from row ``f + u + 1``."""

    observations = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
    frame = _small_frame(observations)
    sessions = [date(2020, 1, 1) + timedelta(days=index) for index in range(len(observations))]
    f_index = 7
    formation_pos = {session: index - f_index for index, session in enumerate(sessions)}
    session_at_offset = {offset: session for session, offset in formation_pos.items()}
    offsets = (-5, -4, -3, -2, -1)

    observed = E._observed_values(
        "A", None, frame, session_at_offset, offsets
    )

    expected = {
        offset: observations[f_index + offset] for offset in offsets
    }
    assert observed.dv == expected
    # The oracle agrees from the raw observation series.
    for offset in offsets:
        assert observed.dv[offset] == oracle.rolling_mean(
            observations, f_index + offset, 1
        )
    # And the raw-session read (the defect) would have double-lagged every value.
    wrong = {
        offset: observations[f_index + offset - 1] for offset in offsets
    }
    assert observed.dv != wrong


def test_m_obs_is_the_realized_formation_row() -> None:
    """``M_obs`` is the realized sub-expression at row ``f`` (never offset-lagged)."""

    frame = _small_frame([1.0, 2.0, 3.0, 4.0])
    sessions = [date(2020, 1, 1) + timedelta(days=index) for index in range(4)]
    formation_pos = {session: index - 3 for index, session in enumerate(sessions)}
    session_at_offset = {offset: session for session, offset in formation_pos.items()}
    m_row = pd.Series({"A": 12.5, "B": math.nan})
    observed = E._observed_values("A", m_row, frame, session_at_offset, (-1,))
    assert observed.M_obs == 12.5
    assert E._observed_values(
        "B", m_row, frame, session_at_offset, (-1,)
    ).M_obs is None


# ---------------------------------------------------------------------------
# Clean V box: a point that equals the realization and the oracle
# ---------------------------------------------------------------------------


def _clean_boxes(case):
    """Reconstruct the certified V box for every key/formation of a case."""

    windows = D.dependency_windows(case.spec.expression)
    overlay = case.store.overlay(pq_id="pqsx", pq_hash="pqsx")
    frames = E._alias_frames(case.spec, case.inputs, overlay)
    v_wide = E._evaluate_subexpression(
        case.spec, windows.v_operand, frames, suffix="v"
    )
    boxes: dict[tuple[date, str], tuple[float, float]] = {}
    realized: dict[tuple[date, str], float] = {}
    for formation in case.fx.FORMATIONS:
        formation_pos = E._formation_positions(case.fx.SESSIONS, formation)
        session_at_offset = {
            offset: session for session, offset in formation_pos.items()
        }
        offsets = E._dv_offsets(windows, formation_pos)
        for key in case.fx.KEYS:
            observed = E._observed_values(
                key, None, case.input_frame, session_at_offset, offsets
            )
            uncertainty = D.input_uncertainty(
                D.STD_X_RANK,
                windows,
                C.SourceSet(),
                formation_pos,
                (key,),
                {key: observed},
            )
            interval = uncertainty.V_interval[key]
            assert interval is not None
            boxes[(formation, key)] = (interval.lo, interval.hi)
            realized[(formation, key)] = float(
                v_wide.loc[pd.Timestamp(formation), key]
            )
    return boxes, realized


def test_clean_v_box_equals_realization_and_oracle(tmp_path: Path, align, oracle) -> None:
    """Every clean key's V box is a point at the realized (and oracle) V."""

    case = align.build_case(tmp_path, seed=20241010, w1=2, w2=6)
    boxes, realized = _clean_boxes(case)
    for formation in case.fx.FORMATIONS:
        f_index = case.fx.SESSIONS.index(formation)
        for key in case.fx.KEYS:
            lo, hi = boxes[(formation, key)]
            # The clean box is a degenerate point (==), not merely a container.
            assert lo == hi
            assert lo == pytest.approx(
                realized[(formation, key)], rel=_REL, abs=_ABS
            )
            reference = oracle.v_ratio(
                list(case.raw[key]), f_index, 2, 6
            )
            assert reference is not None
            assert lo == pytest.approx(reference, rel=_REL, abs=_ABS)


# ---------------------------------------------------------------------------
# Suspect V box: the realized V stays inside the certified interval
# ---------------------------------------------------------------------------


def test_suspect_v_interval_contains_realization(tmp_path: Path, align, oracle) -> None:
    """A dv-cell flag widens the V box but keeps the realized V inside it."""

    case = align.build_case(tmp_path, seed=20241010, w1=2, w2=6)
    windows = D.dependency_windows(case.spec.expression)
    formation = case.fx.FORMATIONS[0]
    f_index = case.fx.SESSIONS.index(formation)
    formation_pos = E._formation_positions(case.fx.SESSIONS, formation)
    session_at_offset = {
        offset: session for session, offset in formation_pos.items()
    }
    offsets = E._dv_offsets(windows, formation_pos)
    key = "A"
    observed = E._observed_values(
        key, None, case.input_frame, session_at_offset, offsets
    )
    # A close-branch transition whose s and prev(s) both fall in the dv cells.
    s = session_at_offset[-2]
    prev_s = session_at_offset[-3]
    next_s = session_at_offset[-1]
    rel = C.s2_relative(1.0)
    transition = C.UncertainTransition(key, s, prev_s, next_s, 1.0, rel)
    source = C.SourceSet(transitions_by_key={key: (transition,)})
    uncertainty = D.input_uncertainty(
        D.STD_X_RANK,
        windows,
        source,
        formation_pos,
        (key,),
        {key: observed},
    )
    interval = uncertainty.V_interval[key]
    assert interval is not None
    assert interval.lo < interval.hi
    reference = oracle.v_ratio(list(case.raw[key]), f_index, 2, 6)
    assert reference is not None
    assert interval.lo <= reference <= interval.hi

    # Independent brute-force vertex enumeration agrees with the closed form.
    short = (-2, -1)  # w1 = 2
    long = tuple(range(-6, 0))  # w2 = 6
    kappas = {-2: (rel.lo, rel.hi), -3: (rel.inv().lo, rel.inv().hi)}
    brute = oracle.suspect_v_interval(observed.dv, short, long, kappas)
    assert brute is not None
    assert interval.lo == pytest.approx(brute[0], rel=_REL, abs=_ABS)
    assert interval.hi == pytest.approx(brute[1], rel=_REL, abs=_ABS)


# ---------------------------------------------------------------------------
# The formerly failing case shape: B's observed group must be reachable
# ---------------------------------------------------------------------------


def _sandwich_scenario(seed: int, *, n: int = 20, groups: int = 5, length: int = 30, w1: int = 2, w2: int = 6):
    """A prev(d) cross-section where the double lag flips a B group.

    The realized factor (hence B's groups) comes from the correct V; the V
    boxes come from ``_observed_values``.  Returns the inputs needed to call
    :func:`sandwich_assignment`, plus a helper that rebuilds the boxes under an
    arbitrary alignment.
    """

    from smart_beta.dataset_v1 import delisting_bridge as B

    rng = np.random.default_rng(seed)
    sessions = [date(2020, 1, 1) + timedelta(days=index) for index in range(length)]
    keys = [f"K{index:02d}" for index in range(n)]
    raw = pd.DataFrame(
        rng.lognormal(0.0, 0.6, size=(length, n)),
        index=pd.to_datetime(sessions),
        columns=keys,
    )
    frame = raw.shift(1)
    frame.iloc[0] = raw.iloc[0]
    f_index = length - 1
    formation_pos = {session: index - f_index for index, session in enumerate(sessions)}
    session_at_offset = {offset: session for session, offset in formation_pos.items()}
    windows = D.Windows(
        numerical_dv_short=((-1 - w1, -1),),
        numerical_dv_long=((-1 - w2, -1),),
    )
    offsets = tuple(sorted(set(range(-w1, 0)) | set(range(-w2, 0))))

    v_obs = {
        key: float(
            np.mean(raw[key].iloc[f_index - w1 : f_index])
            / np.mean(raw[key].iloc[f_index - w2 : f_index])
        )
        for key in keys
    }
    m_obs = {key: float(rng.normal(1.0, 0.3)) for key in keys}
    m_values = np.array([m_obs[key] for key in keys])
    z = (m_values - m_values.mean()) / m_values.std(ddof=0)
    ranks = _average_rank([v_obs[key] for key in keys])
    factor = [z[index] * ranks[index] for index in range(n)]
    panel = pd.DataFrame(
        {
            "date": [pd.Timestamp(date(2020, 1, 1))] * n,
            "stock_id": keys,
            "value": factor,
        }
    )
    groups_map = B._frozen_groups(panel, groups)
    b_groups = {
        key: int(groups_map[(date(2020, 1, 1), key)]) for key in keys
    }

    def observed_for(key: str, mode: str) -> D.ObservedValues:
        if mode == "production":
            return E._observed_values(
                key, None, frame, session_at_offset, offsets
            )
        # mode == "double_lag": the PQ-SX defect (read the raw session).
        dv = {}
        for offset in offsets:
            row_session = session_at_offset.get(offset)
            if row_session is None:
                continue
            stamp = pd.Timestamp(row_session)
            if stamp in frame.index and key in frame.columns:
                dv[offset] = float(frame.at[stamp, key])
        return D.ObservedValues(M_obs=None, dv=dv)

    def boxes(mode: str) -> dict[str, C.PositiveInterval]:
        out: dict[str, C.PositiveInterval] = {}
        for key in keys:
            observed = observed_for(key, mode)
            uncertainty = D.input_uncertainty(
                D.STD_X_RANK,
                windows,
                C.SourceSet(),
                formation_pos,
                (key,),
                {key: observed},
            )
            interval = uncertainty.V_interval[key]
            assert interval is not None
            out[key] = interval
        return out

    return {
        "keys": keys,
        "m_obs": m_obs,
        "v_obs": v_obs,
        "b_groups": b_groups,
        "groups": groups,
        "boxes": boxes,
    }


def _average_rank(values: Sequence[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda index: values[index])
    ranks = [0.0] * len(values)
    position = 0
    while position < len(order):
        end = position
        while (
            end + 1 < len(order)
            and values[order[end + 1]] == values[order[position]]
        ):
            end += 1
        share = (position + end) / 2.0 + 1.0
        for index in order[position : end + 1]:
            ranks[index] = share
        position = end + 1
    return ranks


def test_sandwich_b_group_reachable_and_double_lag_shape(align) -> None:
    """The bridge-aligned boxes keep B's group reachable; the double lag does not."""

    scenario = _sandwich_scenario(seed=0)
    keys = scenario["keys"]

    def call(boxes: Mapping[str, C.PositiveInterval]):
        return SW.sandwich_assignment(
            keys,
            scenario["m_obs"],
            {key: C.PositiveInterval(scenario["m_obs"][key], scenario["m_obs"][key]) for key in keys},
            scenario["v_obs"],
            boxes,
            {},
            keys,
            scenario["b_groups"],
            scenario["groups"],
            {key: 0.0 for key in keys},
            {key: 0.0 for key in keys},
        )

    result = call(scenario["boxes"]("production"))
    assert isinstance(result, SW.SandwichAssignment)
    # The formerly failing shape: restoring the double lag makes B's realized
    # group unreachable.  This is the exact PQ-SX failure.
    with pytest.raises(C.PQv2InvariantError, match="observed group is not reachable"):
        call(scenario["boxes"]("double_lag"))


# ---------------------------------------------------------------------------
# End to end: the prev(d) fixture evaluates ASSESSED
# ---------------------------------------------------------------------------


def test_end_to_end_prev_d_assessed(tmp_path: Path, align) -> None:
    case = align.build_case(tmp_path, seed=20241010, w1=2, w2=6)
    diag = case.diagnostics
    assert diag.not_assessed is None
    assert diag.certified_class == "STD_X_RANK"
    assert diag.assignment_rule == "SANDWICH_CONSERVATIVE_SUPERSET"
    for period in diag.periods:
        assert period.status_t0 == E._envelope.ASSESSED
        assert period.status_ts == E._envelope.ASSESSED


def test_end_to_end_attribution_uses_bridge_rows(tmp_path: Path, align) -> None:
    """The realized M/V sub-expressions at row ``f`` are the bridge-observation V."""

    case = align.build_case(tmp_path, seed=20241010, w1=2, w2=6)
    windows = D.dependency_windows(case.spec.expression)
    overlay = case.store.overlay(pq_id="pqsx", pq_hash="pqsx")
    frames = E._alias_frames(case.spec, case.inputs, overlay)
    v_wide = E._evaluate_subexpression(
        case.spec, windows.v_operand, frames, suffix="v"
    )
    m_wide = E._evaluate_subexpression(
        case.spec, windows.m_operand, frames, suffix="m"
    )
    for formation in case.fx.FORMATIONS:
        f_index = case.fx.SESSIONS.index(formation)
        for key in case.fx.KEYS:
            realized_v = float(v_wide.loc[pd.Timestamp(formation), key])
            reference = align.oracle.v_ratio(
                list(case.raw[key]), f_index, 2, 6
            )
            assert reference is not None
            assert realized_v == pytest.approx(reference, rel=_REL, abs=_ABS)
            assert math.isfinite(float(m_wide.loc[pd.Timestamp(formation), key]))


# ---------------------------------------------------------------------------
# Mutation manifest
# ---------------------------------------------------------------------------


def test_mutations_fixture_has_control_and_required_ids() -> None:
    payload = json.loads(MUTATIONS.read_text(encoding="utf-8"))
    ids = [entry["id"] for entry in payload]
    assert ids[0] == "CONTROL"
    assert {"SX-M1", "SX-M2"} <= set(ids)
    for entry in payload:
        assert {"id", "description", "file", "edits", "test"} <= set(entry)
        assert entry["file"] == "smart_beta/dataset_v1/pqv2_evaluation.py"
        if entry["id"] == "CONTROL":
            assert entry["edits"] == []
        else:
            assert entry["edits"]
            for edit in entry["edits"]:
                assert {"old", "new"} == set(edit)
