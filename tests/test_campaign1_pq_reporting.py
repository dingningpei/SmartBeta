"""Campaign-1 O1 — structural residual cross-sectional influence tests.

Authority: ``worker_tasks/campaign1/task-c1-o1-structural-residual.md`` (O1
repair).  Every test is offline and synthetic: an autouse socket guard and
credential sentinel mirror the PQA/PQB suites.  The real
:mod:`smart_beta.dataset_v1.quarantine` helpers are reused read-only; the
module's bytes are asserted unchanged (its hash pins the admitted overlay
identity ``pq-59c3bd7abe08eef5``).

The frozen section-8 alignment rule forbids a time-series node from consuming a
cross-sectional subtree (``lag(rank(x))`` / ``rolling_mean(rank(x))`` are not
constructible as :class:`FactorSpec`\\ s).  Tests 3 and 4 therefore exercise the
taint pass directly on those literal operator forms via the module's
``_residual_frame`` helper, on an explicit finite value grid, so that the lag
shift and rolling window propagation are covered.  All other tests use the
public :func:`structural_residual_cs` API.
"""

from __future__ import annotations

import hashlib
import json
import re
import socket
import sys
from datetime import date
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import pandas as pd
import pytest

from smart_beta.dataset_v1 import pq_evaluation as PQ
from smart_beta.dataset_v1 import pq_reporting as R
from smart_beta.dataset_v1 import quarantine as Q
from smart_beta.spec.engine import EvidenceClass, TrustedInput
from smart_beta.spec.expression import (
    Add,
    CrossSectional,
    CrossSectionalOp,
    Div,
    Field,
    Lag,
    Rolling,
    RollingOp,
)
from smart_beta.spec.factor_spec import FactorInput, FactorSpec, MissingPolicy
from smart_beta.spec.requirements import (
    DataCapability,
    DataRequirement,
    Frequency,
    ObservationPeriod,
    RevisionPolicy,
    Unit,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
QUARANTINE_PIN = "3d3bff02294b452d797c262f8232a4abaa51d55d05b521f2635156dec3323a1c"
CREDENTIAL_SENTINEL = "c1-o1-credential-sentinel-7e31"

GRID = pd.bdate_range("2020-01-06", periods=6)
KEYS = ("AAA", "BBB", "CCC", "DDD")
INPUT = Q.INPUT_DAILY_TOTAL_RETURN


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_sockets(monkeypatch: pytest.MonkeyPatch):
    def _refuse(*args: object, **kwargs: object) -> None:
        raise RuntimeError("Campaign-1 O1 tests must never open a socket")

    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(socket, "create_connection", _refuse)
    yield


@pytest.fixture(autouse=True)
def _credential_sentinel(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TIINGO_API_KEY", CREDENTIAL_SENTINEL)
    yield


def test_credential_sentinel_never_leaks(capsys: pytest.CaptureFixture[str]) -> None:
    assert CREDENTIAL_SENTINEL == CREDENTIAL_SENTINEL
    captured = capsys.readouterr()
    assert CREDENTIAL_SENTINEL not in captured.out
    assert CREDENTIAL_SENTINEL not in captured.err


# ---------------------------------------------------------------------------
# Synthetic fixture helpers
# ---------------------------------------------------------------------------


def _capability(history: int) -> DataCapability:
    return DataCapability(
        semantic_id=INPUT,
        observation_period=ObservationPeriod.PERIOD,
        units=Unit.FRACTION,
        frequency=Frequency.DAILY,
        history=history,
        has_knowledge_date=True,
        has_positive_vintage_identity=False,
        revision_policies=frozenset({RevisionPolicy.POINT_IN_TIME}),
    )


def _requirement() -> DataRequirement:
    return DataRequirement(
        semantic_id=INPUT,
        frequency=Frequency.DAILY,
        observation_period=ObservationPeriod.PERIOD,
        units=Unit.FRACTION,
        lookback=0,
    )


def _trusted(values: pd.DataFrame) -> TrustedInput:
    return TrustedInput(
        values=values,
        capability=_capability(values.shape[0]),
        evidence_class=EvidenceClass.LIVE_RECORDED,
    )


def _spec(expression: Any) -> FactorSpec:
    return FactorSpec(
        id="c1_o1_probe",
        description="Campaign-1 O1 structural probe",
        expression=expression,
        inputs=(FactorInput("ret", _requirement()),),
        frequency=Frequency.DAILY,
        missing_policy=MissingPolicy.PROPAGATE,
    )


def _overlay(rows: Sequence[tuple[date, str, int]]) -> Q.Overlay:
    frame = pd.DataFrame(
        [(INPUT, pd.Timestamp(d), key, pd.Timestamp(d), state) for d, key, state in rows],
        columns=["input", "row_date", "security_key", "source_session", "state"],
    )
    empty = pd.DataFrame()
    return Q.Overlay(
        flags=empty,
        primitive_states=empty,
        input_states=frame,
        label_states=empty,
        liquidity_exposure=empty,
        identity={},
    )


def _values() -> pd.DataFrame:
    # Non-degenerate so that a cross-sectional ``standardize`` on any row has a
    # non-zero dispersion (a constant row would make it NaN).
    raw = np.arange(1.0, 1.0 + len(GRID) * len(KEYS)).reshape(len(GRID), len(KEYS))
    return pd.DataFrame(raw, index=GRID, columns=list(KEYS))


def _inputs(values: pd.DataFrame | None = None) -> dict[str, TrustedInput]:
    return {INPUT: _trusted(_values() if values is None else values)}


def _counts(frame: pd.DataFrame) -> list[int]:
    return [int(v) for v in frame["residual_cs_cells"]]


# ===========================================================================
# 1. A pure time-series expression gives 0, even when shadow values drift
# ===========================================================================


def test_item01_pure_time_series_zero() -> None:
    spec = _spec(Rolling(RollingOp.MEAN, Field("ret"), 3))
    overlay = _overlay([(GRID[3].date(), "AAA", 1)])
    result = R.structural_residual_cs(spec, overlay=overlay, inputs=_inputs())
    assert _counts(result) == [0, 0, 0, 0, 0, 0]
    assert result.attrs["total_residual_cs_cells"] == 0
    # The legacy shadow-value diagnostic is a different (defective) quantity;
    # the structural diagnostic is 0 by definition (no cross-sectional node).
    legacy = Q.factor_input_states(spec, overlay=overlay, inputs=_inputs())
    assert isinstance(legacy.attrs["residual_cs_cells"], dict)


# ===========================================================================
# 2. rank(x) row seed
# ===========================================================================


def test_item02_rank_row_seed() -> None:
    spec = _spec(CrossSectional(CrossSectionalOp.RANK, Field("ret")))
    overlay = _overlay([(GRID[0].date(), "AAA", 1)])
    result = R.structural_residual_cs(spec, overlay=overlay, inputs=_inputs())
    # row 0: the three CLEAN finite cells count; the quarantined cell does not.
    assert _counts(result) == [3, 0, 0, 0, 0, 0]
    assert result.attrs["total_residual_cs_cells"] == 3
    # the dataframe contract: sorted integer column
    assert list(result.columns) == ["date", "residual_cs_cells"]
    assert str(result["residual_cs_cells"].dtype) == "int64"
    assert list(result["date"]) == sorted(result["date"])


# ===========================================================================
# 3. lag(rank(x), k) shifts the influence by k rows
# ===========================================================================


def test_item03_lag_shifts_influence() -> None:
    # Section-8 forbids this operator order as a FactorSpec; exercise the taint
    # pass on the literal form through the explicit-grid helper.
    expression = Lag(CrossSectional(CrossSectionalOp.RANK, Field("ret")), 2)
    spec = _spec(CrossSectional(CrossSectionalOp.RANK, Field("ret")))
    overlay = _overlay([(GRID[0].date(), "AAA", 1)])
    result = R._residual_frame(expression, _values(), spec, overlay, _inputs())
    assert _counts(result) == [0, 0, 3, 0, 0, 0]
    assert result.attrs["total_residual_cs_cells"] == 3


# ===========================================================================
# 4. rolling_mean(rank(x), w) spreads the influence over the window
# ===========================================================================


def test_item04_rolling_spreads_influence() -> None:
    expression = Rolling(
        RollingOp.MEAN, CrossSectional(CrossSectionalOp.RANK, Field("ret")), 3
    )
    spec = _spec(CrossSectional(CrossSectionalOp.RANK, Field("ret")))
    # The quarantine at row 2 makes rows 2, 3, 4 the full trailing windows
    # that include it: the influence spreads across those three rows.
    overlay = _overlay([(GRID[2].date(), "AAA", 1)])
    result = R._residual_frame(expression, _values(), spec, overlay, _inputs())
    assert _counts(result) == [0, 0, 3, 3, 3, 0]
    assert result.attrs["total_residual_cs_cells"] == 9


# ===========================================================================
# 5. a + rank(x) passes the union through
# ===========================================================================


def test_item05_union_passes_through() -> None:
    spec = _spec(
        Add(Field("ret"), CrossSectional(CrossSectionalOp.RANK, Field("ret")))
    )
    overlay = _overlay([(GRID[0].date(), "AAA", 1)])
    result = R.structural_residual_cs(spec, overlay=overlay, inputs=_inputs())
    assert _counts(result) == [3, 0, 0, 0, 0, 0]


# ===========================================================================
# 6. Nested cross-sectional nodes
# ===========================================================================


def test_item06_nested_cross_sectional() -> None:
    overlay = _overlay([(GRID[0].date(), "AAA", 1)])
    standardize_of_rank = _spec(
        CrossSectional(
            CrossSectionalOp.STANDARDIZE,
            CrossSectional(CrossSectionalOp.RANK, Field("ret")),
        )
    )
    result = R.structural_residual_cs(
        standardize_of_rank, overlay=overlay, inputs=_inputs()
    )
    assert _counts(result) == [3, 0, 0, 0, 0, 0]

    rank_of_rank = _spec(
        CrossSectional(
            CrossSectionalOp.RANK,
            CrossSectional(CrossSectionalOp.RANK, Field("ret")),
        )
    )
    result = R.structural_residual_cs(rank_of_rank, overlay=overlay, inputs=_inputs())
    assert _counts(result) == [3, 0, 0, 0, 0, 0]


# ===========================================================================
# 7. NaN and NOT_APPLICABLE cells are never counted
# ===========================================================================


def test_item07_nan_and_not_applicable_never_counted() -> None:
    raw = np.array(
        [
            [1.0, 0.0, 3.0, 4.0],
            [5.0, 6.0, 7.0, 8.0],
            [5.0, 6.0, np.nan, 8.0],
            [9.0, 10.0, 11.0, 12.0],
            [13.0, 14.0, 15.0, 16.0],
            [17.0, 18.0, 19.0, 20.0],
        ]
    )
    values = pd.DataFrame(raw, index=GRID, columns=list(KEYS))
    # rank(x) + x/x: (row0, BBB) is CLEAN but 0/0 = NaN, and (row2, CCC) is
    # NOT_APPLICABLE (NaN input).  Neither may be counted.
    spec = _spec(
        Add(
            CrossSectional(CrossSectionalOp.RANK, Field("ret")),
            Div(Field("ret"), Field("ret")),
        )
    )
    overlay = _overlay([(GRID[0].date(), "AAA", 1)])
    result = R.structural_residual_cs(
        spec, overlay=overlay, inputs=_inputs(values)
    )
    assert _counts(result) == [2, 0, 0, 0, 0, 0]
    assert result.attrs["total_residual_cs_cells"] == 2


# ===========================================================================
# 8. Determinism, including under TZ=Asia/Tokyo LC_ALL=C
# ===========================================================================


def test_item08_determinism_tz_locale(monkeypatch: pytest.MonkeyPatch) -> None:
    spec = _spec(CrossSectional(CrossSectionalOp.RANK, Field("ret")))
    overlay = _overlay([(GRID[0].date(), "AAA", 1)])
    first = R.structural_residual_cs(spec, overlay=overlay, inputs=_inputs())
    monkeypatch.setenv("TZ", "Asia/Tokyo")
    monkeypatch.setenv("LC_ALL", "C")
    second = R.structural_residual_cs(spec, overlay=overlay, inputs=_inputs())
    pd.testing.assert_frame_equal(first, second)
    assert first.attrs == second.attrs


# ===========================================================================
# 9. PQB reports the structural total, and never reads the legacy attribute
# ===========================================================================


def test_item09_pqb_never_reads_legacy_attribute() -> None:
    source = (REPO_ROOT / "smart_beta/dataset_v1/pq_evaluation.py").read_text(
        encoding="utf-8"
    )
    forbidden = re.compile(
        r"\.attrs\s*(?:\.\s*get\s*\(\s*)?[\"']residual_cs_cells"
    )
    assert not forbidden.search(source)
    assert "_pq_reporting.structural_residual_cs(" in source


def test_item09_pqb_reports_structural_total(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from test_dataset_v1_2_pq_evaluation import _build_case

    sentinel = pd.DataFrame({"date": [], "residual_cs_cells": []})
    sentinel["residual_cs_cells"] = sentinel["residual_cs_cells"].astype("int64")
    sentinel.attrs["total_residual_cs_cells"] = 4242
    monkeypatch.setattr(R, "structural_residual_cs", lambda *a, **k: sentinel)
    case = _build_case(tmp_path)
    assert case.diagnostics.not_assessed is None
    assert case.diagnostics.residual_cs_cells == 4242


# ===========================================================================
# 10. quarantine.py is byte-unchanged; pq_reporting.py is pinned
# ===========================================================================


def test_item10_sealed_hashes() -> None:
    quarantine = hashlib.sha256(
        (REPO_ROOT / "smart_beta/dataset_v1/quarantine.py").read_bytes()
    ).hexdigest()
    assert quarantine == QUARANTINE_PIN
    assert PQ.PINNED_CODE["smart_beta/dataset_v1/quarantine.py"] == QUARANTINE_PIN

    reporting = hashlib.sha256(
        (REPO_ROOT / "smart_beta/dataset_v1/pq_reporting.py").read_bytes()
    ).hexdigest()
    assert PQ.PINNED_CODE["smart_beta/dataset_v1/pq_reporting.py"] == reporting
    assert PQ.verify_pinned_code(REPO_ROOT)["smart_beta/dataset_v1/pq_reporting.py"] == reporting


# ===========================================================================
# 11. Existing PQB machinery keeps its bindings
# ===========================================================================


def test_item11_pqb_bindings_intact() -> None:
    verified = PQ.verify_pinned_code(REPO_ROOT)
    for relative, digest in PQ.PINNED_CODE.items():
        actual = hashlib.sha256((REPO_ROOT / relative).read_bytes()).hexdigest()
        assert actual == digest, relative
    assert len(verified) == len(PQ.PINNED_CODE)


# ===========================================================================
# Mutation fixture contract
# ===========================================================================


def test_mutation_fixture_contract() -> None:
    path = REPO_ROOT / "tests/fixtures/campaign1/o1/mutations.json"
    mutations = json.loads(path.read_text(encoding="utf-8"))
    assert len(mutations) == 10
    assert [m["id"] for m in mutations] == [f"O1-M{i}" for i in range(1, 11)]
    for mutation in mutations:
        assert mutation["edits"], mutation["id"]
        assert mutation["file"] in (
            "smart_beta/dataset_v1/pq_reporting.py",
            "smart_beta/dataset_v1/pq_evaluation.py",
        )
        for edit in mutation["edits"]:
            assert edit["old"] and edit["new"] is not None, mutation["id"]
        assert hasattr(sys.modules[__name__], mutation["test"]), mutation["id"]
