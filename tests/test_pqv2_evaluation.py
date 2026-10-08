"""PQ2-H — P-Q v2 evaluation entry tests (synthetic fixtures only).

Every test is offline and synthetic: an autouse socket guard and credential
sentinel mirror the other P-Q v2 suites.  The end-to-end cases build a tiny
Dataset-v1.2-shaped root through :mod:`tests.fixtures.pqv2.evaluation.fixture`
and run the real D7b pipeline plus the frozen P-Q v2 building blocks.  The
NOT_ASSESSED / period-identity / classification cases drive the frozen
statistics and classification layer directly with hand-built objects.

No production artifact, reserve file, holdout, ``raw/``, ``dsc_evidence`` or
Knowledge-PIT path is read.  No network call is made.
"""

from __future__ import annotations

import dataclasses
import hashlib
import importlib.util
import json
import os
import socket
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd
import pytest

from smart_beta.campaign import diagnostics_store as _store
from smart_beta.dataset_v1 import delisting_bridge as B
from smart_beta.dataset_v1 import pqv2_core as C
from smart_beta.dataset_v1 import pqv2_evaluation as E

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = REPO_ROOT / "tests" / "fixtures" / "pqv2" / "evaluation" / "fixture.py"
MUTATIONS = REPO_ROOT / "tests" / "fixtures" / "pqv2" / "evaluation" / "mutations.json"
CREDENTIAL_SENTINEL = "pqv2h-credential-sentinel-9f31"
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


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_sockets(monkeypatch: pytest.MonkeyPatch):
    def _refuse(*args: object, **kwargs: object) -> None:
        raise RuntimeError("PQ2-H tests must never open a socket")

    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(socket, "create_connection", _refuse)
    yield


@pytest.fixture(autouse=True)
def _credential_sentinel(monkeypatch: pytest.MonkeyPatch):
    for key in _CREDENTIAL_KEYS:
        monkeypatch.setenv(key, CREDENTIAL_SENTINEL)
    yield


COMPAT_PATH = (
    REPO_ROOT / "tests" / "fixtures" / "pqv2" / "evaluation" / "compat.py"
)


def _load_compat():
    module_name = "pqv2h_compat"
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(module_name, COMPAT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def _compat(monkeypatch: pytest.MonkeyPatch):
    """Install the PQ21-D/E/F test doubles only while they are absent (H §2)."""

    _load_compat().install(monkeypatch)
    yield


def test_credential_sentinel_never_leaks() -> None:
    assert E.PQ_METHODOLOGY != CREDENTIAL_SENTINEL


# ---------------------------------------------------------------------------
# Fixture loading
# ---------------------------------------------------------------------------


def _load_fixture():
    module_name = "pqv2h_fixture"
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(module_name, FIXTURE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def fx():
    return _load_fixture()


@dataclasses.dataclass
class Case:
    fx: Any
    root: Path
    store: Any
    spec: Any
    panel: pd.DataFrame
    base: Any
    declaration: Any
    overlay: Any
    inputs: Mapping[str, Any]
    partition: Any
    diagnostics: Any


def _build_case(
    tmp_path: Path,
    *,
    hypothesis: str = "C1-H1",
    sign: int = 1,
    flags: Sequence[tuple[str, int, bool]] = (("A", 261, False),),
    ca_events: Sequence[dict[str, Any]] = (),
    panel=None,
    declaration_overrides: Mapping[str, Any] | None = None,
    overlay_overrides: Mapping[str, Any] | None = None,
    base_overrides: Mapping[str, Any] | None = None,
    d7c_overrides: Mapping[tuple[str, date], float] | None = None,
) -> Case:
    fx = _load_fixture()
    store = fx.SyntheticStore(tmp_path, name="root")
    store.standard()
    for key, session_index, beyond in flags:
        store.add_flag(key, fx.SESSIONS[session_index], beyond=beyond)
    store.ca_events.extend(ca_events)
    for (key, session), value in (d7c_overrides or {}).items():
        store.d7c_overrides[(key, session)] = value
    store.build_d7b()
    store.build_sources()
    pq_bindings, identity_hash = store.build_identity()
    spec = fx.load_campaign_spec(hypothesis)
    if sign != 1:
        spec = dataclasses.replace(spec, sign=sign)
    inputs = fx.build_inputs(store)
    panel_values = panel if panel is not None else fx.factor_panel(spec, inputs, store)
    base = fx.base_declaration(spec, panel_values, **(dict(base_overrides or {})))
    declaration = fx.declaration(
        base,
        identity_hash=identity_hash,
        **(dict(declaration_overrides or {})),
    )
    overlay = store.overlay(pq_id=declaration.pq_id, pq_hash=declaration.pq_hash)
    for name, value in dict(overlay_overrides or {}).items():
        object.__setattr__(overlay, name, value)
    part, _fold, _holdout = fx.partition()
    diagnostics = E.build_pqv2_diagnostics(
        store.root,
        declaration,
        spec,
        panel_values,
        part,
        overlay=overlay,
        inputs=inputs,
        bindings=store.d7b_bindings,
        source_bindings=store.source_bindings,
        pq_bindings=pq_bindings,
        pq_read_records_fn=lambda path: fx.genesis_records(),
    )
    return Case(
        fx,
        store.root,
        store,
        spec,
        panel_values,
        base,
        declaration,
        overlay,
        inputs,
        part,
        diagnostics,
    )


# ---------------------------------------------------------------------------
# Fabricated envelope helpers for the statistics / classification layer
# ---------------------------------------------------------------------------


def _period(
    period: date,
    *,
    L_t0: float = 0.10,
    L_ts: float = 0.10,
    status_t0: str = E._envelope.ASSESSED,
    status_ts: str = E._envelope.ASSESSED,
    reason_t0: str | None = None,
    reason_ts: str | None = None,
    causes_t0: Sequence[str] = (),
    causes_ts: Sequence[str] = (),
    n_unknown_source: int = 0,
    LS_b_t0: float = 0.20,
    LS_b_ts: float = 0.20,
) -> Any:
    compat = _load_compat()
    return compat.CompatPeriod(
        period=period,
        rule="SIMPLE_EXACT",
        winsor_ok=True,
        D_t=0,
        TO_b=0.0,
        TO_hi=0.0,
        changed_b=0,
        union=1,
        LS_b_t0=LS_b_t0,
        LS_b_ts=LS_b_ts,
        L_t0=L_t0,
        L_ts=L_ts,
        status_t0=status_t0,
        status_ts=status_ts,
        reason_t0=reason_t0,
        reason_ts=reason_ts,
        exposure={},
        label_counts={},
        n_unknown_reserve=0,
        causes_t0=tuple(causes_t0),
        causes_ts=tuple(causes_ts),
        n_unknown_source=int(n_unknown_source),
    )


def _envelope(periods: Sequence[E._envelope.EnvelopePeriod]) -> E._envelope.EnvelopeSeries:
    return E._envelope.EnvelopeSeries(
        periods=tuple(periods),
        metric_basis="LONG_SHORT",
        sign=1,
        cost_bps=0.0,
        cost_rate=0.0,
        G=2,
        q=0.0,
        cls=_dependency_simple(),
        rule="SIMPLE_EXACT",
        gate_reason=None,
    )


def _dependency_simple() -> str:
    from smart_beta.dataset_v1 import pqv2_dependency as dependency

    return dependency.SIMPLE


def _b_lookup(periods: Sequence[date], values: Sequence[float]) -> dict[date, float]:
    return {period: value for period, value in zip(periods, values)}


# ===========================================================================
# 1. End-to-end: all-numeric L is computed (SIMPLE and STD_X_RANK)
# ===========================================================================


def test_all_numeric_l_computed_simple(tmp_path: Path) -> None:
    case = _build_case(tmp_path, hypothesis="C1-H1")
    diag = case.diagnostics
    assert diag.not_assessed is None
    assert diag.certified_class == "SIMPLE"
    assert diag.assignment_rule == "SIMPLE_EXACT"
    for name in ("B-T0", "B-TS", "L-T0", "L-TS"):
        cell = diag.cell(name)
        assert cell is not None
        assert cell.status is None, (name, cell.status, cell.reason)
        assert cell.value is not None
        assert cell.t_stat is not None
        assert cell.n_obs == 4
        assert cell.support == 4
        assert cell.estimator == E.ESTIMATOR_IDENTITY
    # The L statistic is the Phase-7 estimator on exactly B's finite index.
    l_t0 = diag.cell("L-T0")
    b_lookup = _b_lookup(diag.b_periods_t0, [0.0] * len(diag.b_periods_t0))
    assert l_t0.period_identities == diag.b_periods_t0
    expected = E._metrics.long_short_mean_tstat(
        np.asarray(
            [period.L_t0 for period in diag.periods if period.period in diag.b_periods_t0],
            dtype="float64",
        )
    )
    assert l_t0.t_stat == pytest.approx(expected.t_stat)
    assert l_t0.value == pytest.approx(expected.value)
    assert b_lookup  # keeps the helper honest


def test_all_numeric_l_computed_sandwich(tmp_path: Path) -> None:
    case = _build_case(tmp_path, hypothesis="C1-H4")
    diag = case.diagnostics
    assert diag.not_assessed is None
    assert diag.certified_class == "STD_X_RANK"
    assert diag.certified_hypothesis == "C1-H4"
    assert diag.assignment_rule == "SANDWICH_CONSERVATIVE_SUPERSET"
    assert diag.relaxations  # the disclosed M/V Cartesian relaxations
    for name in ("L-T0", "L-TS"):
        cell = diag.cell(name)
        assert cell is not None and cell.status is None and cell.value is not None


# ===========================================================================
# 2. L statistic: a NOT_ASSESSED period anywhere in B's index fails the cell
# ===========================================================================


def _estimate_with_not_assessed(position: int) -> tuple[Any, ...]:
    days = [date(2020, 1, 1) + timedelta(days=i) for i in range(4)]
    periods = []
    for index, day in enumerate(days):
        if index == position:
            periods.append(
                _period(
                    day,
                    status_t0=E._envelope.NOT_ASSESSED,
                    reason_t0=C.NotAssessedReason.RESERVE_UNKNOWN,
                    causes_t0=[C.NotAssessedReason.RESERVE_UNKNOWN],
                )
            )
        else:
            periods.append(_period(day))
    envelope = _envelope(periods)
    lookup = {day: 0.20 for day in days}
    return E._estimate_l(envelope, lookup, E.SCENARIO_T0)


@pytest.mark.parametrize("position", [0, 1, 3])
def test_l_period_not_assessed_first_middle_last(position: int) -> None:
    value, t_stat, n_obs, status, reason, period_causes, cause_union = (
        _estimate_with_not_assessed(position)
    )
    assert value is None and t_stat is None
    assert status == E._envelope.NOT_ASSESSED
    assert reason == C.NotAssessedReason.L_PERIOD_NOT_ASSESSED
    assert cause_union == (C.NotAssessedReason.RESERVE_UNKNOWN,)
    assert period_causes[position] == (C.NotAssessedReason.RESERVE_UNKNOWN,)
    assert all(
        causes == ()
        for index, causes in enumerate(period_causes)
        if index != position
    )


def test_b_numeric_while_l_not_assessed_no_substitution() -> None:
    day = date(2020, 1, 1)
    envelope = _envelope(
        [
            _period(
                day,
                status_t0=E._envelope.NOT_ASSESSED,
                reason_t0="PQV2_RESERVE_UNKNOWN",
                causes_t0=["PQV2_RESERVE_UNKNOWN"],
            ),
            _period(day + timedelta(days=1)),
        ]
    )
    lookup = {day: 123.0, day + timedelta(days=1): 456.0}
    value, _t, _n, status, reason, _pc, _cu = E._estimate_l(
        envelope, lookup, E.SCENARIO_T0
    )
    assert value is None
    assert status == E._envelope.NOT_ASSESSED
    assert reason == C.NotAssessedReason.L_PERIOD_NOT_ASSESSED


def test_nan_l_value_fails_closed() -> None:
    days = [date(2020, 1, 1) + timedelta(days=i) for i in range(3)]
    periods = [
        _period(days[0]),
        _period(
            days[1],
            L_t0=float("nan"),
            causes_t0=[C.NotAssessedReason.WINSOR_BLOCK],
        ),
        _period(days[2]),
    ]
    lookup = {day: 0.2 for day in days}
    value, _t, _n, status, reason, _pc, _cu = E._estimate_l(
        _envelope(periods), lookup, E.SCENARIO_T0
    )
    assert value is None
    assert status == E._envelope.NOT_ASSESSED
    assert reason == C.NotAssessedReason.L_PERIOD_NOT_ASSESSED


# ===========================================================================
# 3. L statistic: B/L period order and identity mismatches fail closed
# ===========================================================================


def test_b_l_order_mismatch_fails_closed() -> None:
    days = [date(2020, 1, 1) + timedelta(days=i) for i in range(3)]
    envelope = _envelope(
        [_period(day, L_t0=0.1 * (index + 1)) for index, day in enumerate(days)]
    )
    lookup = {days[0]: 0.2, days[1]: 0.3, days[2]: 0.4}
    reversed_lookup = {days[2]: 0.4, days[1]: 0.3, days[0]: 0.2}
    assert E._estimate_l(envelope, lookup, E.SCENARIO_T0)[3] is None
    with pytest.raises(E.PQv2InvariantError):
        E._estimate_l(envelope, reversed_lookup, E.SCENARIO_T0)


def test_b_l_duplicate_period_fails_closed() -> None:
    day = date(2020, 1, 1)
    envelope = _envelope([_period(day), _period(day), _period(day + timedelta(days=1))])
    lookup = {day: 0.2, day + timedelta(days=1): 0.2}
    with pytest.raises(E.PQv2InvariantError):
        E._estimate_l(envelope, lookup, E.SCENARIO_T0)


def test_b_l_missing_period_fails_closed() -> None:
    days = [date(2020, 1, 1) + timedelta(days=i) for i in range(3)]
    envelope = _envelope([_period(days[0]), _period(days[1])])
    lookup = {day: 0.2 for day in days}
    with pytest.raises(E.PQv2InvariantError):
        E._estimate_l(envelope, lookup, E.SCENARIO_T0)


def test_insufficient_support_not_assessed() -> None:
    day = date(2020, 1, 1)
    envelope = _envelope([_period(day)])
    lookup = {day: 0.2}
    value, t_stat, _n, status, reason, _pc, _cu = E._estimate_l(
        envelope, lookup, E.SCENARIO_T0
    )
    assert value is None and t_stat is None
    assert status == E._envelope.NOT_ASSESSED
    assert reason == C.NotAssessedReason.L_STATISTIC_UNAVAILABLE


def test_partial_period_estimator_rejected() -> None:
    days = [date(2020, 1, 1) + timedelta(days=i) for i in range(4)]
    values = [0.10, 0.20, 0.30, 0.40]
    envelope = _envelope(
        [_period(day, L_t0=value) for day, value in zip(days, values)]
    )
    lookup = {day: 0.5 for day in days}
    value, t_stat, n_obs, status, reason, _pc, _cu = E._estimate_l(
        envelope, lookup, E.SCENARIO_T0
    )
    assert status is None and reason is None
    assert n_obs == 4
    expected = E._metrics.long_short_mean_tstat(np.asarray(values, dtype="float64"))
    assert value == pytest.approx(expected.value)
    assert t_stat == pytest.approx(expected.t_stat)


# ===========================================================================
# 4. Classification: the full M§14 table, typed failure, no ACCEPT
# ===========================================================================


def _diagnostics_with_cells(
    b_t0: float | None, b_ts: float | None, l_t0: float | None, l_ts: float | None
) -> E.PQv2Diagnostics:
    def cell(name: str, value: float | None) -> E.PQv2Cell:
        scenario = "T0" if name.endswith("T0") else "TS"
        estimand = "B" if name.startswith("B") else "L"
        available = value is not None
        return E.PQv2Cell(
            cell=name,
            scenario=scenario,
            estimand=estimand,
            value=value,
            t_stat=1.0 if available else None,
            n_obs=3 if available else 0,
            status=None if available else E._envelope.NOT_ASSESSED,
            reason=None if available else C.NotAssessedReason.B_STATISTIC_UNAVAILABLE,
            n_index=3,
            support=3 if available else 0,
            estimator=E.ESTIMATOR_IDENTITY,
            period_identities=(),
            period_statuses=(),
            period_values=(),
            period_reasons=(),
            declaration_hash="d" * 64,
        )

    return E.PQv2Diagnostics(
        not_assessed=None,
        declaration_hash="d" * 64,
        dataset_identity_hash="i" * 64,
        overlay_pq_hash="p" * 64,
        methodology_sha256=C.METHODOLOGY_SHA256,
        cs_id=C.CS_ID,
        ed_id=C.ED_ID,
        ce_id=C.CE_ID,
        pqv2_code_sha256="c" * 64,
        primary_metric="LONG_SHORT",
        metric_basis="LONG_SHORT",
        basis="GROSS",
        sign=1,
        orientation_rule="PASSTHROUGH",
        screen_id="s",
        screen_policy_hash="ph",
        certified_class="SIMPLE",
        certified_hypothesis="C1-H1",
        assignment_rule="SIMPLE_EXACT",
        relaxations=(),
        factor_panel_sha256="f" * 64,
        index=(),
        cells=(
            cell("B-T0", b_t0),
            cell("B-TS", b_ts),
            cell("L-T0", l_t0),
            cell("L-TS", l_ts),
        ),
        periods=(),
        b_periods_t0=(),
        b_periods_ts=(),
        l_periods_t0=(),
        l_periods_ts=(),
        estimator_identity=E.ESTIMATOR_IDENTITY,
        cost_bps=0.0,
        cost_rate=0.0,
        n_groups=2,
        q=0.0,
        support=(),
        non_claims=E.NON_CLAIMS,
        b_diagnostics_hash=None,
        b_diagnostics=None,
    )


def _screens(b: bool | None, l: bool | None) -> tuple[E.PQv2ScreenOutcome, ...]:
    return (
        E.PQv2ScreenOutcome("B-T0", b),
        E.PQv2ScreenOutcome("B-TS", b),
        E.PQv2ScreenOutcome("L-T0", l),
        E.PQv2ScreenOutcome("L-TS", l),
    )


@pytest.mark.parametrize(
    "b,l,expected",
    [
        (True, True, C.ClassificationStatus.CORRECTION_SENSITIVITY_SCREEN_PASS),
        (True, False, C.ClassificationStatus.QUARANTINE_SENSITIVE),
        (False, True, C.ClassificationStatus.INVARIANT_VIOLATION_FAIL_CLOSED),
        (False, False, C.ClassificationStatus.DEVELOPMENT_SCREEN_FAIL),
        (None, True, C.ClassificationStatus.NOT_ASSESSED),
        (True, None, C.ClassificationStatus.NOT_ASSESSED),
    ],
)
def test_classification_table(b, l, expected) -> None:
    diagnostics = _diagnostics_with_cells(1.0, 1.0, 1.0, 1.0)
    record = E.classify_pqv2(diagnostics, _screens(b, l))
    assert record.status == (expected, expected)


def test_b_fail_l_pass_typed_never_raises() -> None:
    diagnostics = _diagnostics_with_cells(1.0, 1.0, 1.0, 1.0)
    record = E.classify_pqv2(diagnostics, _screens(False, True))
    assert record.status[0] == C.ClassificationStatus.INVARIANT_VIOLATION_FAIL_CLOSED
    assert record.advancement_permitted[0] is False


def test_accept_and_confirmation_are_false() -> None:
    diagnostics = _diagnostics_with_cells(1.0, 1.0, 1.0, 1.0)
    record = E.classify_pqv2(diagnostics, _screens(True, True))
    assert record.accept is False
    assert record.confirmation is False
    assert record.advancement_permitted == (True, True)


def test_scenarios_persisted_independently() -> None:
    diagnostics = _diagnostics_with_cells(1.0, 1.0, 1.0, 1.0)
    screens = (
        E.PQv2ScreenOutcome("B-T0", True),
        E.PQv2ScreenOutcome("B-TS", True),
        E.PQv2ScreenOutcome("L-T0", True),
        E.PQv2ScreenOutcome("L-TS", False),
    )
    record = E.classify_pqv2(diagnostics, screens)
    assert record.status[0] == C.ClassificationStatus.CORRECTION_SENSITIVITY_SCREEN_PASS
    assert record.status[1] == C.ClassificationStatus.QUARANTINE_SENSITIVE
    assert record.advancement_permitted == (True, False)


def test_unavailable_l_cell_is_not_assessed() -> None:
    diagnostics = _diagnostics_with_cells(1.0, 1.0, None, None)
    record = E.classify_pqv2(diagnostics, _screens(True, True))
    assert record.status[0] == C.ClassificationStatus.NOT_ASSESSED


# ===========================================================================
# 5. Rule vocabulary: SIMPLE_EXACT accepted, sandwich accepted, legacy rejected
# ===========================================================================


def test_legacy_simple_rule_tag_fails_closed(tmp_path: Path, monkeypatch) -> None:
    from smart_beta.dataset_v1 import pqv2_assign_simple as simple_mod

    original = simple_mod.simple_assignment

    def _legacy(*args: Any, **kwargs: Any):
        result = original(*args, **kwargs)
        if isinstance(result, C.NotAssessed):
            return result
        return dataclasses.replace(result, rule="SIMPLE")

    monkeypatch.setattr(simple_mod, "simple_assignment", _legacy)
    case = _build_case(tmp_path, hypothesis="C1-H1")
    assert case.diagnostics.not_assessed == C.NotAssessedReason.EXPRESSION_NOT_CERTIFIED


# ===========================================================================
# 6. Gates: sign -1, metric, authority, declaration, upstream hash
# ===========================================================================


def test_sign_minus_one_stays_not_assessed(tmp_path: Path) -> None:
    case = _build_case(tmp_path, hypothesis="C1-H1", sign=-1)
    assert case.diagnostics.not_assessed == C.NotAssessedReason.SIGN_NOT_CERTIFIED


def test_uncertified_metric_blank(tmp_path: Path) -> None:
    case = _build_case(
        tmp_path,
        hypothesis="C1-H1",
        base_overrides={
            "primary_metric": B.spec_mod.MetricKey.IC,
            "basis": "NONE",
        },
    )
    assert case.diagnostics.not_assessed == C.NotAssessedReason.METRIC_NOT_CERTIFIED


def test_authority_tamper_raises(tmp_path: Path, monkeypatch) -> None:
    original = E._core._sha256_file
    reads: list[str] = []
    original_identity = E._pq.verify_dataset_v1_2_identity

    def _tampered(path):
        if str(path).endswith("discovery_dataset_v1_2_pq_v2_methodology.md"):
            return "0" * 64
        return original(path)

    def _spy(*args: Any, **kwargs: Any):
        reads.append("read")
        return original_identity(*args, **kwargs)

    monkeypatch.setattr(E._core, "_sha256_file", _tampered)
    monkeypatch.setattr(E._pq, "verify_dataset_v1_2_identity", _spy)
    with pytest.raises(C.PQv2BindingError):
        _build_case(tmp_path, hypothesis="C1-H1")
    # The authority check fails before any dataset read (H §2 step 1).
    assert reads == []


def test_declaration_identity_mismatch_blank_and_no_reads(
    tmp_path: Path, monkeypatch
) -> None:
    reads: list[str] = []
    original = E._pq.verify_dataset_v1_2_identity

    def _spy(*args: Any, **kwargs: Any):
        reads.append("read")
        return original(*args, **kwargs)

    monkeypatch.setattr(E._pq, "verify_dataset_v1_2_identity", _spy)
    case = _build_case(
        tmp_path,
        hypothesis="C1-H1",
        declaration_overrides={"cs_id": "0" * 64},
    )
    assert case.diagnostics.not_assessed == C.NotAssessedReason.IDENTITY_MISMATCH
    assert reads == []


def test_dataset_identity_mismatch_blank(tmp_path: Path) -> None:
    case = _build_case(
        tmp_path,
        hypothesis="C1-H1",
        declaration_overrides={"dataset_identity_hash": "0" * 64},
    )
    assert (
        case.diagnostics.not_assessed
        == C.NotAssessedReason.DATASET_IDENTITY_MISMATCH
    )


def test_overlay_mismatch_blank(tmp_path: Path) -> None:
    case = _build_case(
        tmp_path,
        hypothesis="C1-H1",
        declaration_overrides={"pq_hash": "correct-hash"},
        overlay_overrides={"pq_hash": "wrong-hash"},
    )
    assert case.diagnostics.not_assessed == C.NotAssessedReason.OVERLAY_MISMATCH


def test_uncertified_expression_blank(tmp_path: Path) -> None:
    fx = _load_fixture()
    spec = dataclasses.replace(fx.load_campaign_spec("C1-H1"), id="uncertified")
    # Perturb the expression so the CE hash no longer matches.
    from smart_beta.spec.expression import Field

    spec = dataclasses.replace(spec, expression=Field("tri"))
    store = fx.SyntheticStore(tmp_path, name="root")
    store.standard()
    store.build_d7b()
    store.build_sources()
    _bindings, identity_hash = store.build_identity()
    inputs = fx.build_inputs(store)
    panel = fx.factor_panel(fx.load_campaign_spec("C1-H1"), inputs, store)
    base = fx.base_declaration(spec, panel)
    declaration = fx.declaration(base, identity_hash=identity_hash)
    overlay = store.overlay(pq_id=declaration.pq_id, pq_hash=declaration.pq_hash)
    part, _fold, _holdout = fx.partition()
    diag = E.build_pqv2_diagnostics(
        store.root, declaration, spec, panel, part,
        overlay=overlay, inputs=inputs, bindings=store.d7b_bindings,
        source_bindings=store.source_bindings, pq_bindings=_bindings,
        pq_read_records_fn=lambda path: fx.genesis_records(),
    )
    assert diag.not_assessed == C.NotAssessedReason.EXPRESSION_NOT_CERTIFIED


def test_upstream_factor_panel_hash_mismatch_raises(tmp_path: Path) -> None:
    with pytest.raises(B.D7bBindingError):
        _build_case(
            tmp_path,
            hypothesis="C1-H1",
            base_overrides={"factor_panel_sha256": "0" * 64},
        )


# ===========================================================================
# 7. B reuse: bit-exact reconciliation and b_diagnostics attachment
# ===========================================================================


def test_b_reconciliation_failure_raises(tmp_path: Path, monkeypatch) -> None:
    original = E._d7b.build_development_diagnostics

    def _perturbed(root, declaration, factor_spec, factor_panel, partition, *, bindings=None):
        diag = original(
            root, declaration, factor_spec, factor_panel, partition, bindings=bindings
        )
        return dataclasses.replace(diag, value_t0=123456.0)

    monkeypatch.setattr(E._d7b, "build_development_diagnostics", _perturbed)
    with pytest.raises(E.PQv2InvariantError):
        _build_case(tmp_path, hypothesis="C1-H1")


def test_b_diagnostics_attached_and_hashed(tmp_path: Path) -> None:
    case = _build_case(tmp_path, hypothesis="C1-H1")
    diag = case.diagnostics
    assert diag.b_diagnostics is not None
    assert diag.b_diagnostics_hash == diag.b_diagnostics.diagnostics_hash
    assert diag.to_dict()["b_diagnostics_hash"] == diag.b_diagnostics_hash


# ===========================================================================
# 8. STD_X_RANK sub-expression consistency assertion
# ===========================================================================


def test_subexpression_consistency_assertion_raises(tmp_path: Path) -> None:
    case = _build_case(tmp_path, hypothesis="C1-H4")
    tampered = case.panel.copy()
    tampered.loc[0, "value"] = 99.0
    spec = case.spec
    base = _load_fixture().base_declaration(spec, tampered)
    fixture = _load_fixture()
    store = case.store
    declaration = fixture.declaration(
        base, identity_hash=store.identity_hash
    )
    overlay = store.overlay(pq_id=declaration.pq_id, pq_hash=declaration.pq_hash)
    with pytest.raises(E.PQv2InvariantError):
        E.build_pqv2_diagnostics(
            store.root, declaration, spec, tampered, case.partition,
            overlay=overlay, inputs=case.inputs, bindings=store.d7b_bindings,
            source_bindings=store.source_bindings, pq_bindings=store.pq_bindings,
            pq_read_records_fn=lambda path: fixture.genesis_records(),
        )


# ===========================================================================
# 9. Reserve UNKNOWN stays typed NOT_ASSESSED
# ===========================================================================


def test_reserve_unknown_period_typed(tmp_path: Path) -> None:
    case = _build_case(
        tmp_path,
        hypothesis="C1-H1",
        flags=(("A", 261, True),),
    )
    diag = case.diagnostics
    reasons = [period.reason_t0 for period in diag.periods]
    assert C.NotAssessedReason.RESERVE_UNKNOWN in reasons
    unknown_period = next(
        period for period in diag.periods
        if period.reason_t0 == C.NotAssessedReason.RESERVE_UNKNOWN
    )
    assert unknown_period.status_t0 == E._envelope.NOT_ASSESSED
    l_cell = diag.cell("L-T0")
    assert l_cell is not None
    assert l_cell.status == E._envelope.NOT_ASSESSED
    assert l_cell.reason == C.NotAssessedReason.L_PERIOD_NOT_ASSESSED


# ===========================================================================
# 10. Diagnostics: hashing, persistence round-trip, determinism, claim language
# ===========================================================================


def test_diagnostics_persist_round_trip(tmp_path: Path) -> None:
    case = _build_case(tmp_path, hypothesis="C1-H1")
    diag = case.diagnostics
    directory = tmp_path / "campaign" / "diagnostics"
    (tmp_path / "campaign").mkdir(parents=True, exist_ok=True)
    digest = _store.persist(diag, directory)
    assert digest == diag.diagnostics_hash
    payload = (directory / f"{digest}.json").read_bytes()
    assert payload == B.canonical_json(diag.to_dict()).encode("utf-8")
    loaded = json.loads(payload)
    assert loaded["declaration_hash"] == diag.declaration_hash
    assert loaded["cs_id"] == C.CS_ID


def test_diagnostics_hash_has_no_timestamps(tmp_path: Path) -> None:
    case = _build_case(tmp_path, hypothesis="C1-H1")
    blob = B.canonical_json(case.diagnostics.to_dict())
    assert "timestamp" not in blob.lower()


def test_non_claims_exact_and_claim_language(tmp_path: Path) -> None:
    case = _build_case(tmp_path, hypothesis="C1-H1")
    diag = case.diagnostics
    assert diag.non_claims == E.NON_CLAIMS
    assert (
        "The correction model is declared, not empirically certified containment."
        in diag.non_claims
    )
    assert any("security identity" in claim for claim in diag.non_claims)
    screens = _screens(True, True)
    record = E.classify_pqv2(diag, screens)
    blob = B.canonical_json(diag.to_dict()) + B.canonical_json(record.to_dict())
    if diag.b_diagnostics is not None:
        blob += B.canonical_json(diag.b_diagnostics.to_dict())
    for prohibited in (
        "ACCEPT",
        "confirmed",
        "bounds the true return",
        "robust to every possible correction",
    ):
        assert prohibited not in blob


def test_period_identities_are_exact_and_ordered(tmp_path: Path) -> None:
    case = _build_case(tmp_path, hypothesis="C1-H1")
    diag = case.diagnostics
    assert diag.b_periods_t0 == diag.index
    assert diag.l_periods_t0 == diag.b_periods_t0
    assert diag.l_periods_ts == diag.b_periods_ts
    # per-period statuses/values/reasons align with the period identities
    l_cell = diag.cell("L-T0")
    assert l_cell is not None
    assert l_cell.period_identities == diag.index
    assert len(l_cell.period_statuses) == len(diag.index)
    assert len(l_cell.period_values) == len(diag.index)
    assert len(l_cell.period_reasons) == len(diag.index)
    assert diag.estimator_identity == E.ESTIMATOR_IDENTITY
    assert diag.methodology_sha256 == C.METHODOLOGY_SHA256
    assert diag.cs_id == C.CS_ID
    assert diag.ed_id == C.ED_ID
    assert diag.ce_id == C.CE_ID
    assert len(diag.pqv2_code_sha256) == 64


def test_period_records_cover_plan_fields(tmp_path: Path) -> None:
    case = _build_case(tmp_path, hypothesis="C1-H1")
    diag = case.diagnostics
    assert len(diag.support) == 4
    for period in diag.periods:
        body = period.to_dict()
        for key in (
            "L_t0",
            "L_ts",
            "LS_b_t0",
            "LS_b_ts",
            "TO_b",
            "TO_hi",
            "D_t",
            "winsor_ok",
            "sigma_ok",
            "exposure",
            "label_counts",
            "position_counts",
            "n_misdated",
            "n_unknown_reserve",
        ):
            assert key in body, key
    # the flag at the 261st session lands in the label window of period 0
    assert diag.periods[0].position_counts[_labels_interior()] >= 1


def _labels_interior() -> str:
    from smart_beta.dataset_v1 import pqv2_labels as labels

    return labels.INTERIOR


# ===========================================================================
# 11. Deterministic replay in fresh processes
# ===========================================================================


def _replay_script() -> str:
    return r"""
import importlib.util, json, sys, tempfile
from pathlib import Path
repo = Path(sys.argv[1])
fix_path = repo / "tests" / "fixtures" / "pqv2" / "evaluation" / "fixture.py"
spec = importlib.util.spec_from_file_location("pqv2h_fixture", fix_path)
fx = importlib.util.module_from_spec(spec)
sys.modules["pqv2h_fixture"] = fx
spec.loader.exec_module(fx)
compat_path = repo / "tests" / "fixtures" / "pqv2" / "evaluation" / "compat.py"
cspec = importlib.util.spec_from_file_location("pqv2h_compat", compat_path)
compat = importlib.util.module_from_spec(cspec)
sys.modules["pqv2h_compat"] = compat
cspec.loader.exec_module(compat)
compat.install_raw()
from smart_beta.dataset_v1 import pqv2_evaluation as E
for hypothesis in ("C1-H1", "C1-H4"):
    tmp = Path(tempfile.mkdtemp())
    store = fx.SyntheticStore(tmp, name="root")
    store.standard()
    store.add_flag("A", fx.SESSIONS[261])
    store.build_d7b(); store.build_sources()
    pq_bindings, identity = store.build_identity()
    specf = fx.load_campaign_spec(hypothesis)
    inputs = fx.build_inputs(store)
    panel = fx.factor_panel(specf, inputs, store)
    base = fx.base_declaration(specf, panel)
    decl = fx.declaration(base, identity_hash=identity)
    overlay = store.overlay(pq_id=decl.pq_id, pq_hash=decl.pq_hash)
    part, _f, _h = fx.partition()
    diag = E.build_pqv2_diagnostics(
        store.root, decl, specf, panel, part,
        overlay=overlay, inputs=inputs, bindings=store.d7b_bindings,
        source_bindings=store.source_bindings, pq_bindings=pq_bindings,
        pq_read_records_fn=lambda path: fx.genesis_records(),
    )
    print(diag.diagnostics_hash)
"""


def test_deterministic_replay_identical_hash(tmp_path: Path) -> None:
    script = _replay_script()
    env = os.environ.copy()
    for key in _CREDENTIAL_KEYS:
        env.pop(key, None)
    runs = []
    for _ in range(2):
        proc = subprocess.run(
            [sys.executable, "-c", script, str(REPO_ROOT)],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        assert proc.returncode == 0, proc.stderr
        runs.append(proc.stdout.strip().splitlines())
    assert len(runs[0]) == 2
    assert runs[0] == runs[1]


def _replay_script_with_causes() -> str:
    return r"""
import importlib.util, sys, tempfile
from pathlib import Path
repo = Path(sys.argv[1])
fix_path = repo / "tests" / "fixtures" / "pqv2" / "evaluation" / "fixture.py"
spec = importlib.util.spec_from_file_location("pqv2h_fixture", fix_path)
fx = importlib.util.module_from_spec(spec)
sys.modules["pqv2h_fixture"] = fx
spec.loader.exec_module(fx)
compat_path = repo / "tests" / "fixtures" / "pqv2" / "evaluation" / "compat.py"
cspec = importlib.util.spec_from_file_location("pqv2h_compat", compat_path)
compat = importlib.util.module_from_spec(cspec)
sys.modules["pqv2h_compat"] = compat
cspec.loader.exec_module(compat)
compat.install_raw()
from smart_beta.dataset_v1 import pqv2_evaluation as E
for hypothesis in ("C1-H1", "C1-H4"):
    tmp = Path(tempfile.mkdtemp())
    store = fx.SyntheticStore(tmp, name="root")
    store.standard()
    store.add_flag("A", fx.SESSIONS[239])
    store.d7c_overrides[("A", fx.SESSIONS[240])] = float("nan")
    store.build_d7b(); store.build_sources()
    pq_bindings, identity = store.build_identity()
    specf = fx.load_campaign_spec(hypothesis)
    inputs = fx.build_inputs(store)
    panel = fx.factor_panel(specf, inputs, store)
    base = fx.base_declaration(specf, panel)
    decl = fx.declaration(base, identity_hash=identity)
    overlay = store.overlay(pq_id=decl.pq_id, pq_hash=decl.pq_hash)
    part, _f, _h = fx.partition()
    diag = E.build_pqv2_diagnostics(
        store.root, decl, specf, panel, part,
        overlay=overlay, inputs=inputs, bindings=store.d7b_bindings,
        source_bindings=store.source_bindings, pq_bindings=pq_bindings,
        pq_read_records_fn=lambda path: fx.genesis_records(),
    )
    assert diag.periods[0].causes_t0 == (E._core.NotAssessedReason.SOURCE_UNRESOLVED,)
    print(diag.diagnostics_hash)
"""


def test_deterministic_replay_with_causes_identical_hash(tmp_path: Path) -> None:
    script = _replay_script_with_causes()
    env = os.environ.copy()
    for key in _CREDENTIAL_KEYS:
        env.pop(key, None)
    runs = []
    for _ in range(2):
        proc = subprocess.run(
            [sys.executable, "-c", script, str(REPO_ROOT)],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        assert proc.returncode == 0, proc.stderr
        runs.append(proc.stdout.strip().splitlines())
    assert len(runs[0]) == 2
    assert runs[0] == runs[1]


# ===========================================================================
# 12. Gap-aware HAC is never used and never imported
# ===========================================================================


def test_gap_hac_not_imported_in_fresh_process() -> None:
    script = (
        r'''
import importlib.util, sys
sys.argv = [sys.argv[0]]
from pathlib import Path
compat_path = Path(__COMPAT_PATH__)
cspec = importlib.util.spec_from_file_location("pqv2h_compat", compat_path)
compat = importlib.util.module_from_spec(cspec)
sys.modules["pqv2h_compat"] = compat
cspec.loader.exec_module(compat)
compat.install_raw()
from smart_beta.dataset_v1 import pqv2_evaluation as E
assert "smart_beta.engines.gap_hac" not in sys.modules, "gap_hac imported"
# Exercise the estimator path as well.
from datetime import date, timedelta
day = date(2020, 1, 1)
periods = []
for i in range(3):
    periods.append(compat.CompatPeriod(
        period=day + timedelta(days=i), rule="SIMPLE_EXACT", winsor_ok=True,
        D_t=0, TO_b=0.0, TO_hi=0.0, changed_b=0, union=1,
        LS_b_t0=1.0, LS_b_ts=1.0, L_t0=0.1, L_ts=0.1,
        status_t0=E._envelope.ASSESSED, status_ts=E._envelope.ASSESSED,
        reason_t0=None, reason_ts=None, exposure={}, label_counts={},
        n_unknown_reserve=0,
    ))
env = E._envelope.EnvelopeSeries(
    periods=tuple(periods), metric_basis="LONG_SHORT", sign=1, cost_bps=0.0,
    cost_rate=0.0, G=2, q=0.0, cls="SIMPLE", rule="SIMPLE_EXACT",
    gate_reason=None,
)
E._estimate_l(env, {p.period: 0.2 for p in periods}, "T0")
assert "smart_beta.engines.gap_hac" not in sys.modules, "gap_hac imported by estimator"
print("ok")
'''.replace("__COMPAT_PATH__", repr(str(COMPAT_PATH)))
    )
    env = os.environ.copy()
    for key in _CREDENTIAL_KEYS:
        env.pop(key, None)
    env["PYTHONPATH"] = str(REPO_ROOT)
    proc = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip().endswith("ok")


def test_pq_evaluation_not_imported() -> None:
    script = r"""
import sys
from smart_beta.dataset_v1 import pqv2_evaluation as E
assert "smart_beta.dataset_v1.pq_evaluation" not in sys.modules, "pq_evaluation imported"
print("ok")
"""
    env = os.environ.copy()
    for key in _CREDENTIAL_KEYS:
        env.pop(key, None)
    env["PYTHONPATH"] = str(REPO_ROOT)
    proc = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip().endswith("ok")


# ===========================================================================
# 14. PQ21-H v2.1 causes: pass-through, persistence and identity
# ===========================================================================


SOURCE = C.NotAssessedReason.SOURCE_UNRESOLVED
RESERVE = C.NotAssessedReason.RESERVE_UNKNOWN


def _assert_period_causes(diag: Any, index: int, expected: Sequence[str]) -> None:
    period = diag.periods[index]
    assert tuple(period.causes_t0) == tuple(expected)
    assert tuple(period.causes_ts) == tuple(expected)
    assert period.reason_t0 == C.CauseSet.of(*expected).primary


def test_source_only_factor_input_cause_end_to_end(tmp_path: Path) -> None:
    fx = _load_fixture()
    case = _build_case(
        tmp_path,
        hypothesis="C1-H1",
        flags=(("A", 239, False),),
        d7c_overrides={("A", fx.SESSIONS[240]): float("nan")},
    )
    diag = case.diagnostics
    assert diag.not_assessed is None
    _assert_period_causes(diag, 0, [SOURCE])
    # No layer may relabel a SOURCE-only cell as RESERVE.
    for period in diag.periods:
        assert RESERVE not in period.causes_t0
        assert period.reason_t0 != RESERVE
    l_cell = diag.cell("L-T0")
    assert l_cell is not None
    assert l_cell.status == E._envelope.NOT_ASSESSED
    assert l_cell.reason == C.NotAssessedReason.L_PERIOD_NOT_ASSESSED
    assert l_cell.cause_union == (SOURCE,)


def test_reserve_only_factor_input_cause_end_to_end(tmp_path: Path) -> None:
    case = _build_case(
        tmp_path,
        hypothesis="C1-H1",
        flags=(("B", 239, True),),
    )
    diag = case.diagnostics
    _assert_period_causes(diag, 0, [RESERVE])
    l_cell = diag.cell("L-T0")
    assert l_cell is not None
    assert l_cell.cause_union == (RESERVE,)


def test_both_causes_factor_input_end_to_end(tmp_path: Path) -> None:
    fx = _load_fixture()
    case = _build_case(
        tmp_path,
        hypothesis="C1-H1",
        flags=(("A", 239, False), ("B", 239, True)),
        d7c_overrides={("A", fx.SESSIONS[240]): float("nan")},
    )
    diag = case.diagnostics
    _assert_period_causes(diag, 0, [RESERVE, SOURCE])
    assert diag.periods[0].reason_t0 == RESERVE
    l_cell = diag.cell("L-T0")
    assert l_cell is not None
    assert l_cell.cause_union == (RESERVE, SOURCE)


def test_source_label_cause_end_to_end(tmp_path: Path) -> None:
    fx = _load_fixture()
    case = _build_case(
        tmp_path,
        hypothesis="C1-H1",
        flags=(("A", 261, False),),
        d7c_overrides={("A", fx.SESSIONS[262]): float("nan")},
    )
    diag = case.diagnostics
    _assert_period_causes(diag, 0, [SOURCE])
    l_cell = diag.cell("L-T0")
    assert l_cell is not None
    assert l_cell.cause_union == (SOURCE,)


def test_unrelated_unresolved_flag_leaves_periods_assessed(tmp_path: Path) -> None:
    fx = _load_fixture()
    case = _build_case(
        tmp_path,
        hypothesis="C1-H1",
        flags=(("A", 255, False),),
        d7c_overrides={("A", fx.SESSIONS[256]): float("nan")},
    )
    diag = case.diagnostics
    for period in diag.periods:
        assert tuple(period.causes_t0) == ()
        assert tuple(period.causes_ts) == ()
    l_cell = diag.cell("L-T0")
    assert l_cell is not None
    assert l_cell.status is None
    assert l_cell.cause_union == ()


def test_v2_methodology_identity_rejected_no_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reads: list[str] = []
    original = E._pq.verify_dataset_v1_2_identity

    def _spy(*args: Any, **kwargs: Any):
        reads.append("read")
        return original(*args, **kwargs)

    monkeypatch.setattr(E._pq, "verify_dataset_v1_2_identity", _spy)
    case = _build_case(
        tmp_path,
        hypothesis="C1-H1",
        declaration_overrides={
            "methodology_sha256": C.AUTHORITY_V2.methodology_sha256
        },
    )
    assert case.diagnostics.not_assessed == C.NotAssessedReason.IDENTITY_MISMATCH
    assert reads == []


def test_v2_ed_id_identity_rejected_no_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reads: list[str] = []
    original = E._pq.verify_dataset_v1_2_identity

    def _spy(*args: Any, **kwargs: Any):
        reads.append("read")
        return original(*args, **kwargs)

    monkeypatch.setattr(E._pq, "verify_dataset_v1_2_identity", _spy)
    case = _build_case(
        tmp_path,
        hypothesis="C1-H1",
        declaration_overrides={"ed_id": C.AUTHORITY_V2.ed_id},
    )
    assert case.diagnostics.not_assessed == C.NotAssessedReason.IDENTITY_MISMATCH
    assert reads == []


# -- session-only source-set projection ------------------------------------


def test_session_only_source_set_rejects_bare_session() -> None:
    bare = date(2020, 1, 1)
    source_set = C.SourceSet(unknown_by_key={"A": (bare,)})
    with pytest.raises(C.PQv2InputError):
        E._session_only_source_set(source_set)


def test_session_only_source_set_passes_flags_through() -> None:
    flag = C.UnresolvedFlag(
        security_key="A",
        s=date(2020, 1, 1),
        prev_s=date(2019, 12, 31),
        state=C.SourceState.UNKNOWN_SOURCE,
    )
    source_set = C.SourceSet(unknown_by_key={"A": (flag,)})
    projected = E._session_only_source_set(source_set)
    assert projected.unknown_by_key["A"] == (flag,)
    assert projected.unknown_by_key["A"][0] is flag


# -- per-period record and L-cell cause persistence ------------------------


def test_period_record_carries_causes_and_counts() -> None:
    period = _period(
        date(2020, 1, 1),
        status_t0=E._envelope.NOT_ASSESSED,
        status_ts=E._envelope.NOT_ASSESSED,
        reason_t0=RESERVE,
        reason_ts=RESERVE,
        causes_t0=[RESERVE, SOURCE],
        causes_ts=[RESERVE, SOURCE],
        n_unknown_source=2,
    )
    record = E._period_record(period, {}, None)
    assert record.causes_t0 == (RESERVE, SOURCE)
    assert record.causes_ts == (RESERVE, SOURCE)
    assert record.n_unknown_source == 2
    assert record.reason_t0 == RESERVE
    body = record.to_dict()
    assert body["causes_t0"] == [RESERVE, SOURCE]
    assert body["causes_ts"] == [RESERVE, SOURCE]
    assert body["n_unknown_source"] == 2


def test_l_cell_cause_union_retains_both_causes() -> None:
    days = [date(2020, 1, 1) + timedelta(days=i) for i in range(3)]
    periods = [
        _period(
            days[0],
            status_t0=E._envelope.NOT_ASSESSED,
            status_ts=E._envelope.NOT_ASSESSED,
            reason_t0=RESERVE,
            reason_ts=RESERVE,
            causes_t0=[RESERVE, SOURCE],
            causes_ts=[RESERVE, SOURCE],
        ),
        _period(days[1]),
        _period(days[2]),
    ]
    lookup = {day: 0.2 for day in days}
    value, _t, _n, status, reason, period_causes, cause_union = E._estimate_l(
        _envelope(periods), lookup, E.SCENARIO_T0
    )
    assert value is None
    assert status == E._envelope.NOT_ASSESSED
    assert reason == C.NotAssessedReason.L_PERIOD_NOT_ASSESSED
    assert period_causes[0] == (RESERVE, SOURCE)
    assert cause_union == (RESERVE, SOURCE)


def test_diagnostics_persist_round_trip_with_causes(tmp_path: Path) -> None:
    fx = _load_fixture()
    case = _build_case(
        tmp_path,
        hypothesis="C1-H1",
        flags=(("A", 239, False),),
        d7c_overrides={("A", fx.SESSIONS[240]): float("nan")},
    )
    diag = case.diagnostics
    directory = tmp_path / "campaign" / "diagnostics"
    (tmp_path / "campaign").mkdir(parents=True, exist_ok=True)
    digest = _store.persist(diag, directory)
    loaded = json.loads((directory / f"{digest}.json").read_text(encoding="utf-8"))
    first = loaded["periods"][0]
    assert first["causes_t0"] == [SOURCE]
    assert first["causes_ts"] == [SOURCE]
    assert "n_unknown_source" in first
    l_t0 = next(cell for cell in loaded["cells"] if cell["cell"] == "L-T0")
    assert l_t0["cause_union"] == [SOURCE]


# ===========================================================================
# 13. Mutation control and runner support
# ===========================================================================


def test_control_is_green(tmp_path: Path) -> None:
    case = _build_case(tmp_path, hypothesis="C1-H1")
    assert case.diagnostics.not_assessed is None
    assert case.diagnostics.cell("L-T0").status is None


def test_mutations_fixture_has_control_and_required_ids() -> None:
    payload = json.loads(MUTATIONS.read_text(encoding="utf-8"))
    ids = [entry["id"] for entry in payload]
    assert ids[0] == "CONTROL"
    required = {
        "V2-M17",
        "V2-M19",
        "H-M1",
        "H-M2",
        "H-M3",
        "H-M4",
        "H-M5",
        "H-M6",
        "H-M7",
        "H-M8",
        "H-M9",
        "H-M10",
        "H-M11",
        "H-M12",
        "H-M13",
        "H-M14",
        "H-M15",
        "H-M16",
        "H-M17",
        "H-M18",
        "H-M19",
        "H-M20",
        "H-M21",
        "V21-M08",
        "H21-M1",
        "H21-M2",
        "H21-M3",
        "H21-M4",
    }
    assert required.issubset(set(ids))
    for entry in payload:
        assert {"id", "description", "file", "edits", "test"} <= set(entry)
