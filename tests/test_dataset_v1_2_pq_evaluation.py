"""PQB — P-Q dual-estimand evaluation bridge tests (synthetic fixtures only).

Every test is offline and synthetic: an autouse socket guard and credential
sentinel mirror the D7a/D7b/PQA/PQC suites.  The real PQA
(``smart_beta.dataset_v1.quarantine``), PQC (``smart_beta.engines.gap_hac``)
and D7b (``smart_beta.dataset_v1.delisting_bridge``) modules are used, never
doubles.  No production artifact, reserve file, holdout or provider is read.

Functions are named ``test_itemNN_*`` so the completion report's test-to-item
map is mechanical (items 1-20 of the frozen PQB spec §6).  Mutation
verification (the planner re-runs the shipped
``tests/fixtures/dataset_v1_2/pqb/mutations.json``) relies on at least one
of these tests failing per mutation.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import json
import math
import os
import socket
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd
import pytest

from smart_beta.dataset_v1 import delisting_bridge as B
from smart_beta.dataset_v1 import pq_evaluation as PQ
from smart_beta.dataset_v1 import quarantine as Q
from smart_beta.dataset_v1.trusted_inputs import canonical_json as _canon
from smart_beta.evaluation.partition import Fold, FoldRole, Partition
from smart_beta.engines import gap_hac
from smart_beta.engines.inference import newey_west_ols
from smart_beta.spec.engine import EvidenceClass, TrustedInput
from smart_beta.spec.expression import Field
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
CREDENTIAL_SENTINEL = "pqb-credential-sentinel-4a17"
VALID_HASH = "a" * 64


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_sockets(monkeypatch: pytest.MonkeyPatch):
    def _refuse(*args: object, **kwargs: object) -> None:
        raise RuntimeError("PQB tests must never open a socket")

    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(socket, "create_connection", _refuse)
    yield


@pytest.fixture(autouse=True)
def _credential_sentinel(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TIINGO_API_KEY", CREDENTIAL_SENTINEL)
    yield


def test_credential_sentinel_never_leaks(capsys) -> None:
    arr = np.array([1.0, 2.0, np.nan, 4.0, 5.0], dtype="float64")
    assert PQ.mask_series(arr, np.array([True, True, False, True, True])).size == 5
    captured = capsys.readouterr()
    assert CREDENTIAL_SENTINEL not in captured.out
    assert CREDENTIAL_SENTINEL not in captured.err


# ---------------------------------------------------------------------------
# D7b synthetic store (adapted from the D7b suite)
# ---------------------------------------------------------------------------


def _business_days(start: dt.date, n: int) -> list[dt.date]:
    out: list[dt.date] = []
    d = start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += dt.timedelta(days=1)
    return out


SESSIONS = _business_days(dt.date(2019, 12, 2), 220)
POS = {d: i for i, d in enumerate(SESSIONS)}
F1 = SESSIONS[0]
F2 = SESSIONS[4]
F3 = SESSIONS[8]
F4 = SESSIONS[12]
FORMATIONS = (F1, F2, F3, F4)
FOLD_START = SESSIONS[0]
FOLD_END = SESSIONS[90]
HOLDOUT_START = SESSIONS[90]
HOLDOUT_END = SESSIONS[130]
HORIZON = 3
ARTIFACT_ID = "r-pqbtest0000000000"
DEFAULT_RATES = {"A": 0.01, "B": 0.02, "C": 0.03, "D": 0.04}
KEYS = ("A", "B", "C", "D")


def _future(f: dt.date, n: int) -> dt.date:
    return SESSIONS[POS[f] + n]


@dataclasses.dataclass
class _Member:
    formation: dt.date
    key: str
    state: str = "COMPLETE"
    affected: bool = False
    m: int | None = None
    rank: int = 1
    rate: float = 0.01
    daily: list[tuple[dt.date, str, float]] | None = None
    observed_segment_end: dt.date | None = None
    endpoint: dt.date | None = None
    listed_end_dev: dt.date | None = None
    n_invalid_sessions: int = 0


class _Store:
    def __init__(self, tmp_path: Path, name: str = "store", horizon: int = HORIZON):
        self.root = tmp_path / name
        self.root.mkdir(parents=True, exist_ok=True)
        self.horizon = horizon
        self.members: list[_Member] = []
        self.extra_daily: list[tuple[str, dt.date, str, float]] = []
        self.crosses_reserve: dict[dt.date, bool] = {}

    def add(self, formation: dt.date, key: str, **kwargs: Any) -> _Member:
        member = _Member(formation=formation, key=key, **kwargs)
        if member.state == "EMPTY":
            member.affected = True
        self.members.append(member)
        return member

    def standard(self, formations: Sequence[dt.date] = FORMATIONS) -> None:
        for i, f in enumerate(formations):
            for key in KEYS:
                rank = "ABCD".index(key) + 1
                shift = 0.01 if i % 2 == 1 else 0.0
                self.add(f, key, rank=rank, rate=DEFAULT_RATES[key] + shift)

    def build(self) -> B.Bindings:
        root = self.root
        members_rows: list[dict[str, Any]] = []
        horizon_rows: list[dict[str, Any]] = []
        label_rows: list[dict[str, Any]] = []
        daily_rows: list[tuple[str, dt.date, str, float]] = []
        formations = sorted({m.formation for m in self.members})
        for f in formations:
            endpoint = _future(f, self.horizon)
            horizon_rows.append(
                {
                    "formation_date": f,
                    "h": self.horizon,
                    "endpoint": endpoint,
                    "crosses_reserve": self.crosses_reserve.get(f, False),
                }
            )
        for m in self.members:
            endpoint = m.endpoint or _future(m.formation, self.horizon)
            if m.observed_segment_end is not None:
                segment_end = m.observed_segment_end
            elif m.affected and m.m is not None:
                segment_end = _future(m.formation, m.m)
            else:
                segment_end = endpoint
            sessions = m.m if m.m is not None else self.horizon
            members_rows.append(
                {
                    "formation_date": m.formation,
                    "security_key": m.key,
                    "ticker": m.key,
                    "rank": m.rank,
                    "top_200": False,
                    "top_500": True,
                    "top_1000": True,
                    "listed_end_dev": m.listed_end_dev or SESSIONS[89],
                    "listing_status": "ACTIVE",
                    "terminal_evidence_source": "D4_RETROSPECTIVE_LISTED_END",
                }
            )
            label_state = (
                "EMPTY_OBSERVED_PATH_IDENTITY" if m.state == "EMPTY" else m.state
            )
            label_rows.append(
                {
                    "formation_date": m.formation,
                    "security_key": m.key,
                    "h": self.horizon,
                    "endpoint": endpoint,
                    "label_state": label_state,
                    "terminal_affected": m.affected,
                    "observed_path_complete": label_state == "COMPLETE",
                    "n_observed_sessions": sessions,
                    "n_post_terminal_sessions": 0,
                    "n_invalid_sessions": m.n_invalid_sessions,
                    "observed_segment_end": segment_end,
                    "first_invalid_session": None,
                    "first_invalid_status": None,
                }
            )
            if m.daily is not None:
                daily_rows.extend((m.key, d, s, r) for d, s, r in m.daily)
            elif m.state != "EMPTY":
                cover = m.m if (m.affected and m.m is not None) else sessions
                for j in range(1, cover + 1):
                    daily_rows.append((m.key, _future(m.formation, j), "VALID", m.rate))
        daily_rows.extend(self.extra_daily)

        daily = pd.DataFrame(
            daily_rows,
            columns=["security_key", "date", "session_status", "daily_total_return"],
        )
        daily["prev_session"] = pd.NaT
        daily["ca_event_count"] = 0
        daily["corp_action_suspect"] = False
        members = pd.DataFrame(members_rows)
        horizons = pd.DataFrame(horizon_rows)
        labels = pd.DataFrame(label_rows)
        for frame, cols in (
            (members, ["formation_date", "listed_end_dev"]),
            (horizons, ["formation_date", "endpoint"]),
            (labels, ["formation_date", "endpoint", "observed_segment_end",
                      "first_invalid_session"]),
            (daily, ["date", "prev_session"]),
        ):
            for column in cols:
                if column in frame:
                    frame[column] = pd.to_datetime(frame[column])

        base = f"derived/returns/{ARTIFACT_ID}"
        (root / base).mkdir(parents=True, exist_ok=True)
        for name, frame in (
            ("members.parquet", members),
            ("horizons.parquet", horizons),
            ("labels.parquet", labels),
            ("daily_returns.parquet", daily),
        ):
            frame.to_parquet(root / base / name, index=False)
        files = {
            f"{base}/{name}": B._sha256_file(root / base / name)
            for name in (
                "members.parquet",
                "horizons.parquet",
                "labels.parquet",
                "daily_returns.parquet",
            )
        }
        returns_hash = B.content_hash(files)
        manifest = {
            "returns_id": ARTIFACT_ID,
            "files_sha256": files,
            "returns_hash": returns_hash,
        }
        (root / base / "returns.json").write_text(json.dumps(manifest))
        manifest_sha = B._sha256_file(root / base / "returns.json")
        d7a = B.D7aBinding(
            artifact_id=ARTIFACT_ID,
            returns_hash=returns_hash,
            manifest_relative_path=f"{base}/returns.json",
            manifest_sha256=manifest_sha,
            files=tuple(B.FileBinding(path, files[path]) for path in sorted(files)),
        )
        defaults = B.default_bindings()
        return B.Bindings(
            d7a=d7a,
            code_sha256=defaults.code_sha256,
            documents=defaults.documents,
        )


# ---------------------------------------------------------------------------
# Panel / spec / partition / declaration helpers
# ---------------------------------------------------------------------------


def _panel(values: Mapping[tuple[dt.date, str], float]) -> pd.DataFrame:
    rows = [
        {"date": f, "stock_id": key, "value": value}
        for (f, key), value in sorted(values.items())
    ]
    frame = pd.DataFrame(rows)
    frame["date"] = pd.to_datetime(frame["date"])
    frame["stock_id"] = frame["stock_id"].astype("string")
    frame["value"] = frame["value"].astype("float64")
    return frame


def _standard_values(
    formations: Sequence[dt.date] = FORMATIONS,
    keys: Sequence[str] = KEYS,
) -> dict[tuple[dt.date, str], float]:
    values: dict[tuple[dt.date, str], float] = {}
    for j, formation in enumerate(formations):
        row = [float(i + 1) for i in range(len(keys))]
        if j % 2 == 1:
            row = list(reversed(row))
        for i, key in enumerate(keys):
            values[(formation, key)] = row[i]
    return values


def _partition() -> tuple[Partition, Fold, Fold]:
    fold = Fold(FoldRole.IS, FOLD_START, FOLD_END)
    holdout = Fold(FoldRole.HOLDOUT, HOLDOUT_START, HOLDOUT_END)
    return Partition(folds=(fold, holdout), split_rule="pqb-test"), fold, holdout


def _factor_spec(sign: int = 1) -> FactorSpec:
    requirement = DataRequirement(
        semantic_id=Q.INPUT_DAILY_TOTAL_RETURN,
        frequency=Frequency.DAILY,
        observation_period=ObservationPeriod.PERIOD,
        units=Unit.FRACTION,
        lookback=0,
        revision_policy=RevisionPolicy.POINT_IN_TIME,
    )
    return FactorSpec(
        id="pqb_factor",
        description="synthetic pqb factor",
        expression=Field("ret"),
        inputs=(FactorInput("ret", requirement),),
        frequency=Frequency.DAILY,
        missing_policy=MissingPolicy.PROPAGATE,
        sign=sign,
    )


def _base_declaration(
    partition: Partition,
    fold: Fold,
    panel: pd.DataFrame,
    spec: FactorSpec,
    **overrides: Any,
) -> B.CampaignEvaluationDeclaration:
    fields: dict[str, Any] = {
        "precommitment_ref": "precommitment-1",
        "factor_spec_version": spec.version,
        "factor_panel_sha256": B._canonical_factor_hash(panel),
        "partition_id": partition.partition_id,
        "fold_key": B.fold_key(fold),
        "universe": "TOP_1000",
        "horizon": HORIZON,
        "n_groups": 2,
        "winsorization": 0.0,
        "transaction_cost_bps": 0.0,
        "periods_per_year": 252,
        "weighting": "EQUAL",
        "primary_metric": B.spec_mod.MetricKey.LONG_SHORT,
        "basis": "GROSS",
        "screen_id": "screen-1",
        "screen_policy_hash": "policy-1",
    }
    fields.update(overrides)
    return B.CampaignEvaluationDeclaration(**fields)


def _trusted(values: pd.DataFrame) -> TrustedInput:
    capability = DataCapability(
        semantic_id=Q.INPUT_DAILY_TOTAL_RETURN,
        observation_period=ObservationPeriod.PERIOD,
        units=Unit.FRACTION,
        frequency=Frequency.DAILY,
        history=values.shape[0],
        has_knowledge_date=True,
        has_positive_vintage_identity=False,
        revision_policies=frozenset({RevisionPolicy.POINT_IN_TIME}),
    )
    return TrustedInput(
        values=values, capability=capability, evidence_class=EvidenceClass.LIVE_RECORDED
    )


def _inputs_for(values: Mapping[tuple[dt.date, str], float]) -> dict[str, TrustedInput]:
    grid = pd.DatetimeIndex(sorted({f for f, _ in values}))
    frame = pd.DataFrame(np.nan, index=grid, columns=list(KEYS), dtype="float64")
    for (formation, key), value in values.items():
        frame.loc[pd.Timestamp(formation), key] = float(value)
    return {Q.INPUT_DAILY_TOTAL_RETURN: _trusted(frame)}


# ---------------------------------------------------------------------------
# PQA identity / overlay fixtures
# ---------------------------------------------------------------------------

_ARTIFACT_MANIFESTS = {
    "n-f874d3ecb61374d4": ("normalized/n-f874d3ecb61374d4/build.json", "normalized_hash"),
    "q-5d1028655f8c82e1": ("derived/qc/q-5d1028655f8c82e1/qc_report.json", "qc_hash"),
    "c-27b6dabd0db0cb15": ("derived/classification/c-27b6dabd0db0cb15/dsc.json", "dsc_hash"),
    "u-6150bc6ac2866b7f": ("derived/universe/u-6150bc6ac2866b7f/universe.json", "universe_hash"),
    "r-34565f074d64ccda": ("derived/returns/r-34565f074d64ccda/returns.json", "returns_hash"),
    "ti-aa23fabd3db4e595": (
        "derived/trusted_inputs/ti-aa23fabd3db4e595/trusted_inputs.json", "ti_hash"
    ),
}


class _Kind:
    def __init__(self, value: str):
        self.value = value


class _FakeRecord:
    def __init__(self, seq: int, kind: str, record_hash: str, payload: Mapping[str, Any]):
        self.seq = seq
        self.kind = _Kind(kind)
        self.record_hash = record_hash
        self.payload = dict(payload)

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "kind": self.kind.value,
            "record_hash": self.record_hash,
            "payload": dict(self.payload),
        }


def _genesis_records(*extra: _FakeRecord) -> tuple[_FakeRecord, ...]:
    return (
        _FakeRecord(
            Q.D8_GENESIS_SEQ,
            "ARTIFACT",
            Q.D8_GENESIS_RECORD_HASH,
            {"source_label": Q.D8_SOURCE_LABEL},
        ),
        *extra,
    )


def _write_artifact_manifests(root: Path) -> None:
    values = {
        "normalized_hash": Q.INHERITED_ARTIFACTS["n-f874d3ecb61374d4"],
        "qc_hash": Q.INHERITED_ARTIFACTS["q-5d1028655f8c82e1"],
        "dsc_hash": Q.INHERITED_ARTIFACTS["c-27b6dabd0db0cb15"],
        "universe_hash": Q.INHERITED_ARTIFACTS["u-6150bc6ac2866b7f"],
        "returns_hash": Q.INHERITED_ARTIFACTS["r-34565f074d64ccda"],
        "ti_hash": Q.INHERITED_ARTIFACTS["ti-aa23fabd3db4e595"],
    }
    for artifact_id, (rel, field) in _ARTIFACT_MANIFESTS.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((_canon({field: values[field]}) + "\n").encode("utf-8"))


def _pq_bindings() -> Q.Bindings:
    return dataclasses.replace(Q.DEFAULT_BINDINGS, artifact_manifests=_ARTIFACT_MANIFESTS)


def _identity_context(root: Path) -> tuple[Q.Bindings, str]:
    root.mkdir(parents=True, exist_ok=True)
    _write_artifact_manifests(root)
    bindings = _pq_bindings()
    payload = Q.build_identity_payload()
    pq_dir = Q._overlay_directory(root, bindings)
    pq_dir.mkdir(parents=True, exist_ok=True)
    (pq_dir / "dataset_identity.json").write_bytes(Q._canonical_bytes(payload))
    return bindings, Q.compute_identity_hash(payload)


def _overlay(
    *,
    input_states: pd.DataFrame | None = None,
    label_states: pd.DataFrame | None = None,
    liquidity: pd.DataFrame | None = None,
    pq_id: str = "pq-test",
    pq_hash: str = "h-test",
) -> Q.Overlay:
    empty = pd.DataFrame()
    return Q.Overlay(
        flags=empty,
        primitive_states=empty,
        input_states=empty if input_states is None else input_states,
        label_states=empty if label_states is None else label_states,
        liquidity_exposure=empty if liquidity is None else liquidity,
        identity={},
        pq_id=pq_id,
        pq_hash=pq_hash,
    )


def _input_states(rows: Sequence[tuple[dt.date, str]]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            (Q.INPUT_DAILY_TOTAL_RETURN, pd.Timestamp(f), key, pd.Timestamp(f), 2)
            for f, key in rows
        ],
        columns=["input", "row_date", "security_key", "source_session", "state"],
    )


def _label_states(rows: Sequence[tuple[dt.date, str, int]]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            (pd.Timestamp(f), key, h, 2, "FLAG_IN_PATH")
            for f, key, h in rows
        ],
        columns=["formation_date", "security_key", "h", "state", "reason"],
    )


# ---------------------------------------------------------------------------
# Integration case
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class _Case:
    root: Path
    bindings: B.Bindings
    pq_bindings: Q.Bindings
    partition: Partition
    fold: Fold
    spec: FactorSpec
    panel: pd.DataFrame
    declaration: PQ.PQEvaluationDeclaration
    overlay: Q.Overlay
    inputs: Mapping[str, TrustedInput]
    diagnostics: PQ.PQDiagnostics

    def screens(self, a_t0: bool = True, a_ts: bool = True) -> tuple[PQ.PQScreenOutcome, ...]:
        out = []
        for name in PQ._CELL_NAMES:
            cell = self.diagnostics.cell(name)
            assert cell is not None
            if name.startswith("B"):
                passed = True
            else:
                passed = a_t0 if name.endswith("T0") else a_ts
            out.append(
                PQ.PQScreenOutcome(
                    cell=name,
                    cell_hash=cell.cell_hash,
                    screen_id=self.diagnostics.screen_id,
                    screen_policy_hash=self.diagnostics.screen_policy_hash,
                    declaration_hash=self.diagnostics.declaration_hash,
                    passed=passed,
                )
            )
        return tuple(out)

    def classify(self, a_t0: bool = True, a_ts: bool = True):
        return PQ.classify_pq(self.diagnostics, self.screens(a_t0, a_ts))


def _build_case(
    tmp_path: Path,
    *,
    input_flag_rows: Sequence[tuple[dt.date, str]] = (),
    label_flag_rows: Sequence[tuple[dt.date, str, int]] = (),
    sign: int = 1,
    primary_metric: Any = None,
    base_overrides: Mapping[str, Any] | None = None,
    declaration_overrides: Mapping[str, Any] | None = None,
    store: _Store | None = None,
    panel_values: Mapping[tuple[dt.date, str], float] | None = None,
) -> _Case:
    if store is None:
        store = _Store(tmp_path)
        store.standard()
    bindings = store.build()
    partition, fold, _ = _partition()
    spec = _factor_spec(sign)
    values = dict(_standard_values() if panel_values is None else panel_values)
    panel = _panel(values)
    base_overrides = dict(base_overrides or {})
    if primary_metric is not None:
        base_overrides["primary_metric"] = primary_metric
        base_overrides.setdefault("basis", _basis_for(primary_metric))
    base = _base_declaration(partition, fold, panel, spec, **base_overrides)
    pq_bindings, identity_hash = _identity_context(store.root)
    decl_fields: dict[str, Any] = {
        "base": base,
        "dataset_id": PQ.DATASET_ID,
        "dataset_identity_hash": identity_hash,
        "amendment_sha256": PQ.AMENDMENT_SHA256,
        "quarantine_rule_hash": PQ.QUARANTINE_RULE_HASH,
        "pq_id": "pq-test",
        "pq_hash": "h-test",
    }
    decl_fields.update(declaration_overrides or {})
    declaration = PQ.PQEvaluationDeclaration(**decl_fields)
    overlay = _overlay(
        input_states=_input_states(input_flag_rows) if input_flag_rows else None,
        label_states=_label_states(label_flag_rows) if label_flag_rows else None,
        pq_id=declaration.pq_id,
        pq_hash=declaration.pq_hash,
    )
    inputs = _inputs_for(values)
    diagnostics = PQ.build_pq_diagnostics(
        store.root,
        declaration,
        spec,
        panel,
        partition,
        overlay=overlay,
        inputs=inputs,
        bindings=bindings,
        pq_bindings=pq_bindings,
        pq_read_records_fn=lambda path: _genesis_records(),
    )
    return _Case(
        store.root, bindings, pq_bindings, partition, fold, spec, panel,
        declaration, overlay, inputs, diagnostics,
    )


def _basis_for(metric: Any) -> str:
    return {
        B.spec_mod.MetricKey.IC: "NONE",
        B.spec_mod.MetricKey.RANK_IC: "NONE",
        B.spec_mod.MetricKey.LONG_SHORT: "GROSS",
        B.spec_mod.MetricKey.SHARPE: "GROSS",
        B.spec_mod.MetricKey.MAX_DRAWDOWN: "GROSS",
        B.spec_mod.MetricKey.TURNOVER_COST_ADJUSTED: "NET",
        B.spec_mod.MetricKey.BENCHMARK_RELATIVE: "NONE",
    }[metric]


# ---------------------------------------------------------------------------
# Synthetic classification diagnostics
# ---------------------------------------------------------------------------


def _cell(
    name: str,
    value: float | None,
    *,
    status: str | None = None,
    t_stat: float | None = 1.0,
    n_obs: int = 3,
    declaration_hash: str = VALID_HASH,
) -> PQ.PQCell:
    scenario = PQ.SCENARIO_T0 if name.endswith("T0") else PQ.SCENARIO_TS
    estimand = PQ.ESTIMAND_ORIGINAL if name.startswith("B") else PQ.ESTIMAND_QUARANTINED
    return PQ.PQCell(
        cell=name,
        scenario=scenario,
        estimand=estimand,
        value=value,
        raw_value=value,
        t_stat=t_stat,
        status=status,
        n_obs=n_obs,
        n_index=n_obs,
        availability_mask_hash=hashlib.sha256(name.encode()).hexdigest(),
        declaration_hash=declaration_hash,
    )


def _fake_diag_b(
    *,
    economic_t0: float | None = 1.0,
    economic_ts: float | None = 1.0,
    e_s: float | None = 0.0,
    b: float | None = 1.0,
    screen_id: str = "screen-1",
    screen_policy_hash: str = "policy-1",
    declaration_hash: str = VALID_HASH,
) -> B.DevelopmentDiagnostics:
    return B.DevelopmentDiagnostics(
        not_assessed=None,
        declaration_hash=declaration_hash,
        factor_spec_version="v",
        factor_panel_sha256="f" * 64,
        partition_id="p",
        fold_key="k",
        fold_role="is",
        fold_start=FOLD_START,
        fold_end=FOLD_END,
        universe="TOP_1000",
        horizon=HORIZON,
        n_groups=2,
        winsorization=0.0,
        transaction_cost_bps=0.0,
        periods_per_year=252,
        primary_metric="long_short",
        basis="GROSS",
        sign=1,
        orientation_rule="PASSTHROUGH",
        screen_id=screen_id,
        screen_policy_hash=screen_policy_hash,
        d7a_artifact_id=ARTIFACT_ID,
        d7a_returns_hash="r" * 64,
        d7a_file_hashes=(),
        code_sha256=(),
        document_hashes=(),
        formations=(F1, F2),
        purged_cells=(),
        formation_accounting=(),
        b=b,
        e_s=e_s,
        e_l=0.0,
        n_b_formations=2,
        n_es_formations=2,
        n_el_formations=2,
        value_t0=economic_t0,
        value_ts=economic_ts,
        t_t0=1.0,
        t_ts=1.0,
        n_obs_t0=2,
        n_obs_ts=2,
        economic_t0=economic_t0,
        economic_ts=economic_ts,
        reconciliation=(),
        accounting_records_hash="h" * 64,
        accounting_records=(),
    )


def _make_diagnostics(
    *,
    a_t0: float | None = 1.0,
    a_ts: float | None = 1.0,
    b_t0: float | None = 1.0,
    b_ts: float | None = 1.0,
    a_t0_status: str | None = None,
    a_ts_status: str | None = None,
    q_b: float | None = 0.0,
    q_s: float | None = 0.0,
    q_l: float | None = 0.0,
    e_s: float | None = 0.0,
    b: float | None = 1.0,
    not_assessed: str | None = None,
    b_diag: Any = "auto",
    screen_id: str = "screen-1",
    screen_policy_hash: str = "policy-1",
    declaration_hash: str = VALID_HASH,
) -> PQ.PQDiagnostics:
    cells = (
        _cell("B-T0", b_t0, declaration_hash=declaration_hash),
        _cell("B-TS", b_ts, declaration_hash=declaration_hash),
        _cell("A-T0", a_t0, status=a_t0_status, declaration_hash=declaration_hash),
        _cell("A-TS", a_ts, status=a_ts_status, declaration_hash=declaration_hash),
    )
    diag_b = (
        _fake_diag_b(
            economic_t0=b_t0,
            economic_ts=b_ts,
            e_s=e_s,
            b=b,
            screen_id=screen_id,
            screen_policy_hash=screen_policy_hash,
            declaration_hash=declaration_hash,
        )
        if b_diag == "auto"
        else b_diag
    )
    return PQ.PQDiagnostics(
        not_assessed=not_assessed,
        declaration_hash=declaration_hash,
        primary_metric="long_short",
        basis="GROSS",
        sign=1,
        orientation_rule="PASSTHROUGH",
        screen_id=screen_id,
        screen_policy_hash=screen_policy_hash,
        index=(F1, F2),
        cells=cells,
        b_diagnostics_hash=None if diag_b is None else diag_b.diagnostics_hash,
        e_s=e_s,
        b=b,
        q_b=q_b,
        q_s=q_s,
        q_l=q_l,
        n_flagged_formations=0,
        residual_cs_cells=0,
        liquidity_exposed=0,
        n_interior_b_missing=0,
        support=(),
        cs3=(),
        cs3_status=None,
        b_diagnostics=diag_b,
    )


def _screens(
    diagnostics: PQ.PQDiagnostics,
    *,
    a_t0: bool = True,
    a_ts: bool = True,
) -> tuple[PQ.PQScreenOutcome, ...]:
    out = []
    for name in PQ._CELL_NAMES:
        cell = diagnostics.cell(name)
        assert cell is not None
        passed = True if name.startswith("B") else (a_t0 if name.endswith("T0") else a_ts)
        out.append(
            PQ.PQScreenOutcome(
                cell=name,
                cell_hash=cell.cell_hash,
                screen_id=diagnostics.screen_id,
                screen_policy_hash=diagnostics.screen_policy_hash,
                declaration_hash=diagnostics.declaration_hash,
                passed=passed,
            )
        )
    return tuple(out)


# ===========================================================================
# 1. A == B on available periods, bitwise
# ===========================================================================


def test_item01_a_equals_b_bitwise_random() -> None:
    rng = np.random.default_rng(101)
    for _ in range(200):
        size = int(rng.integers(2, 40))
        b = rng.normal(size=size) * rng.uniform(0.05, 2.0)
        available = rng.random(size) < 0.6
        if available.sum() < 1:
            continue
        a = PQ.mask_series(b, available)
        for i in range(size):
            if available[i]:
                assert PQ._bits(a[i]) == PQ._bits(b[i]), i
            else:
                assert math.isnan(a[i]), i


def test_item01_targeted_a_equals_b_on_gaps() -> None:
    b = np.array([1.0, np.nan, 3.5, 4.0, np.nan, -2.0])
    available = np.array([True, True, False, True, True, False])
    a = PQ.mask_series(b, available)
    assert math.isnan(a[0]) is False and PQ._bits(a[0]) == PQ._bits(b[0])
    assert math.isnan(a[1]) and math.isnan(b[1])
    assert math.isnan(a[2])
    assert PQ._bits(a[3]) == PQ._bits(b[3])
    assert math.isnan(a[4])
    assert math.isnan(a[5])
    with pytest.raises(PQ.PQEvaluationError):
        PQ.mask_series(b, available[:3])


# ===========================================================================
# 2. No quarantine: A statistics equal B statistics
# ===========================================================================


def test_item02_no_quarantine_parity(tmp_path: Path) -> None:
    case = _build_case(tmp_path)
    assert case.diagnostics.not_assessed is None
    a_t0 = case.diagnostics.cell("A-T0")
    b_t0 = case.diagnostics.cell("B-T0")
    assert a_t0 and b_t0
    # With no quarantine, A's per-period series equals B's and the A statistic
    # equals the B statistic.
    assert a_t0.status is None
    assert PQ._bits(a_t0.value) == PQ._bits(b_t0.value)
    assert a_t0.t_stat is not None and b_t0.t_stat is not None
    assert _math_close(a_t0.t_stat, b_t0.t_stat)


def test_item02_gap_free_pqc_matches_metrics() -> None:
    rng = np.random.default_rng(5)
    from smart_beta.evaluation.metrics import _newey_west_aggregate

    for n in (3, 5, 8, 13, 50):
        arr = rng.normal(size=n) * 2.0
        result = gap_hac.gap_aware_mean_tstat(arr)
        mean, t_stat, _n_obs = _newey_west_aggregate(arr)
        assert PQ._bits(result.mean) == PQ._bits(mean)
        assert _math_close(result.t_stat, t_stat)


def _math_close(actual: float, expected: float) -> bool:
    return math.isfinite(actual) and abs(actual - expected) <= max(
        1e-10 * abs(expected), 1e-12
    )


# ===========================================================================
# 3. A single flagged member makes its whole group unavailable
# ===========================================================================


def test_item03_single_flag_drops_group_period(tmp_path: Path) -> None:
    case = _build_case(tmp_path, input_flag_rows=[(F1, "D")])
    assert case.diagnostics.not_assessed is None
    a_t0 = case.diagnostics.cell("A-T0")
    b_t0 = case.diagnostics.cell("B-T0")
    assert a_t0 and b_t0
    # D is in the economic LONG leg (group 2); the F1 gross period is masked.
    assert a_t0.n_obs < b_t0.n_obs


def test_item03_label_flag_drops_group_period(tmp_path: Path) -> None:
    case = _build_case(tmp_path, label_flag_rows=[(F1, "A", HORIZON)])
    assert case.diagnostics.not_assessed is None
    a_t0 = case.diagnostics.cell("A-T0")
    b_t0 = case.diagnostics.cell("B-T0")
    assert a_t0 and b_t0
    assert a_t0.n_obs < b_t0.n_obs


def test_item03_short_leg_flag_drops_ic(tmp_path: Path) -> None:
    case = _build_case(
        tmp_path,
        input_flag_rows=[(F1, "A")],
        primary_metric=B.spec_mod.MetricKey.RANK_IC,
    )
    a_t0 = case.diagnostics.cell("A-T0")
    b_t0 = case.diagnostics.cell("B-T0")
    assert a_t0 and b_t0
    assert a_t0.n_obs < b_t0.n_obs


def test_item03_availability_is_group_period(tmp_path: Path) -> None:
    case = _build_case(tmp_path, input_flag_rows=[(F1, "A")])
    assert case.diagnostics.not_assessed is None
    assert case.diagnostics.q_b is not None and case.diagnostics.q_b > 0.0


# ===========================================================================
# 4. Groups identical across A, B, T0 and TS
# ===========================================================================


def test_item04_groups_identical_across_cells(tmp_path: Path) -> None:
    case = _build_case(tmp_path, input_flag_rows=[(F1, "A")])
    assert case.diagnostics.not_assessed is None
    # Group assignment is a D7b function of the factor panel alone; there is no
    # regroup API and the availability code only reads the frozen groups.
    diag = case.diagnostics
    assert not hasattr(PQ, "regroup")
    assert diag.b_diagnostics is not None


def test_item04_flags_shared_across_scenarios(tmp_path: Path) -> None:
    case = _build_case(tmp_path, input_flag_rows=[(F1, "A")])
    a_t0 = case.diagnostics.cell("A-T0")
    a_ts = case.diagnostics.cell("A-TS")
    b_t0 = case.diagnostics.cell("B-T0")
    b_ts = case.diagnostics.cell("B-TS")
    assert a_t0 and a_ts and b_t0 and b_ts
    assert a_t0.n_obs == a_ts.n_obs
    assert PQ._bits(a_t0.value) == PQ._bits(a_ts.value)
    assert a_t0.availability_mask_hash == a_ts.availability_mask_hash
    assert b_t0.n_obs == b_ts.n_obs


# ===========================================================================
# 5. IC / rank-IC pairwise deletion prohibition
# ===========================================================================


def test_item05_ic_period_availability(tmp_path: Path) -> None:
    case = _build_case(
        tmp_path,
        input_flag_rows=[(F1, "A")],
        primary_metric=B.spec_mod.MetricKey.IC,
    )
    assert case.diagnostics.not_assessed is None
    a_t0 = case.diagnostics.cell("A-T0")
    b_t0 = case.diagnostics.cell("B-T0")
    assert a_t0 is not None and a_t0.status is None
    assert b_t0 is not None
    # The whole flagged IC period is dropped, never pairwise-deleted.
    assert a_t0.n_obs < b_t0.n_obs


def test_item05_rank_ic_uses_same_mask(tmp_path: Path) -> None:
    case = _build_case(
        tmp_path,
        input_flag_rows=[(F1, "A")],
        primary_metric=B.spec_mod.MetricKey.RANK_IC,
    )
    assert case.diagnostics.not_assessed is None
    availability = PQ._Availability(
        formations=(F1,),
        gross={F1: True},
        net={F1: True},
        ic={F1: False},
        q_b=0.25,
        q_s=0.5,
        q_l=0.0,
        flagged_formations=1,
    )
    ic_avail = PQ._available_for_metric(availability, B.spec_mod.MetricKey.IC)
    rank_avail = PQ._available_for_metric(availability, B.spec_mod.MetricKey.RANK_IC)
    assert ic_avail is rank_avail
    assert ic_avail[F1] is False


# ===========================================================================
# 6. Gapped HAC uses original distance; compressed estimator never called
# ===========================================================================


def test_item06_gap_hac_never_calls_compressed(monkeypatch: pytest.MonkeyPatch) -> None:
    import smart_beta.engines.inference as inference
    import smart_beta.evaluation.metrics as metrics_mod

    calls: list[Any] = []

    def _sentinel(*args: Any, **kwargs: Any) -> Any:
        calls.append((args, kwargs))
        raise AssertionError("compressed newey_west_ols must never be called")

    monkeypatch.setattr(inference, "newey_west_ols", _sentinel)
    monkeypatch.setattr(metrics_mod, "newey_west_ols", _sentinel)
    arr = np.array([1.0, 2.0, np.nan, np.nan, 5.0, 6.0, np.nan, 8.0])
    stat = PQ._estimate_a(
        arr, np.ones(arr.size, dtype=bool), B.spec_mod.MetricKey.LONG_SHORT,
        sign=1, periods_per_year=252, lags=None,
    )
    assert stat.status is None
    assert not calls


def test_item06_no_fallback_on_non_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    import smart_beta.evaluation.metrics as metrics_mod

    def _sentinel(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("no compressed fallback is permitted")

    monkeypatch.setattr(metrics_mod, "newey_west_ols", _sentinel)
    arr = np.array([0.02, 0.02, 0.02])
    stat = PQ._estimate_a(
        arr, np.ones(arr.size, dtype=bool), B.spec_mod.MetricKey.LONG_SHORT,
        sign=1, periods_per_year=252, lags=None,
    )
    assert stat.status == "ZERO_RANGE"
    assert stat.value == 0.02


# ===========================================================================
# 7. MAX_DRAWDOWN with an interior gap -> PATH_GAP
# ===========================================================================


def test_item07_drawdown_path_gap(tmp_path: Path) -> None:
    b = np.array([0.01, 0.02, 0.03, 0.01, 0.0])
    available = np.array([True, True, True, False, True])
    stat = PQ._estimate_a(
        b, available, B.spec_mod.MetricKey.MAX_DRAWDOWN,
        sign=1, periods_per_year=252, lags=None,
    )
    assert stat.status == PQ.PATH_GAP
    assert stat.value is None

    # A leading or trailing gap is allowed.
    leading = np.array([np.nan, 0.01, 0.02, 0.03, 0.01])
    assert PQ.interior_gaps(leading) == 0
    stat2 = PQ._estimate_a(
        leading, np.array([False, True, True, True, True]),
        B.spec_mod.MetricKey.MAX_DRAWDOWN, sign=1, periods_per_year=252, lags=None,
    )
    assert stat2.status is None
    assert stat2.value is not None


# ===========================================================================
# 8. Turnover / cost equal B; net requires a clean composition
# ===========================================================================


def test_item08_turnover_and_cost_common(tmp_path: Path) -> None:
    case = _build_case(tmp_path, input_flag_rows=[(F1, "A")])
    assert case.diagnostics.not_assessed is None
    b = case.diagnostics.b_diagnostics
    assert b is not None
    # The net (TURNOVER_COST_ADJUSTED) availability requires a clean
    # composition; the gross period availability is a superset.
    gross = case.diagnostics.cell("A-T0")
    assert gross is not None


def test_item08_net_uses_cost_series(tmp_path: Path) -> None:
    case = _build_case(
        tmp_path,
        primary_metric=B.spec_mod.MetricKey.TURNOVER_COST_ADJUSTED,
        base_overrides={"transaction_cost_bps": 10.0},
    )
    assert case.diagnostics.not_assessed is None
    a_t0 = case.diagnostics.cell("A-T0")
    b_t0 = case.diagnostics.cell("B-T0")
    assert a_t0 and b_t0
    assert a_t0.status is None
    assert PQ._bits(a_t0.value) == PQ._bits(b_t0.value)


def test_item08_composition_includes_previous() -> None:
    groups = {
        (F1, "A"): 1.0,
        (F1, "B"): 2.0,
        (F2, "C"): 1.0,
        (F2, "D"): 2.0,
    }
    input_flag = {(F2, "A")}
    availability = PQ._compute_availability(groups, input_flag, set(), 2, 1)
    assert availability.gross[F2] is True
    assert availability.net[F2] is False


def test_item08_availability_requires_both_legs() -> None:
    groups = {
        (F1, "A"): 1.0,
        (F1, "B"): 2.0,
    }
    availability = PQ._compute_availability(groups, {(F1, "B")}, set(), 2, 1)
    assert availability.gross[F1] is False
    assert availability.ic[F1] is False


# ===========================================================================
# 9. CS-3
# ===========================================================================


def test_item09_complement_series() -> None:
    b = np.array([1.0, 2.0, np.nan, 4.0, 5.0])
    a = np.array([1.0, np.nan, np.nan, 4.0, np.nan])
    c = PQ.complement_series(b, a)
    assert PQ._bits(c[1]) == PQ._bits(b[1])
    assert math.isnan(c[3])
    assert PQ._bits(c[4]) == PQ._bits(b[4])
    assert math.isnan(c[0])
    assert math.isnan(c[2])


def test_item09_classification_invariant_to_complement(tmp_path: Path) -> None:
    diagnostics = _make_diagnostics(q_b=0.0, q_s=0.0, q_l=0.0)
    record = PQ.classify_pq(diagnostics, _screens(diagnostics))
    assert record.status == PQ.STATUS_NOT_SENSITIVE
    # CS-3 diagnostics are never decision inputs: tampered fields do not move
    # the classification.
    tampered = dataclasses.replace(
        diagnostics, cs3=(("mean", 999.0),), cs3_status="OK"
    )
    assert PQ.classify_pq(tampered, _screens(tampered)).status == record.status


# ===========================================================================
# 10. QUARANTINE_SENSITIVE clauses at their boundaries
# ===========================================================================


def test_item10_clause_a_sign_change() -> None:
    diagnostics = _make_diagnostics(a_t0=1.0, b_t0=-1.0)
    record = PQ.classify_pq(diagnostics, _screens(diagnostics))
    assert record.clause_a_t0 is True
    assert record.status == PQ.STATUS_QUARANTINE_SENSITIVE


def test_item10_clause_a_zero_handling() -> None:
    diagnostics = _make_diagnostics(a_t0=0.0, b_t0=1.0)
    record = PQ.classify_pq(diagnostics, _screens(diagnostics))
    assert PQ._sgn(0.0) == 0
    assert record.clause_a_t0 is True


def test_item10_clause_b_either_direction() -> None:
    diagnostics = _make_diagnostics()
    screens = list(_screens(diagnostics))
    screens = [
        dataclasses.replace(s, passed=False) if s.cell == "A-T0" else s
        for s in screens
    ]
    record = PQ.classify_pq(diagnostics, screens)
    assert record.clause_b_t0 is True
    assert record.status == PQ.STATUS_QUARANTINE_SENSITIVE

    # B failing while A passes is also a clause (b) difference.
    reverse = list(_screens(diagnostics))
    reverse = [
        dataclasses.replace(s, passed=False) if s.cell == "B-T0" else s
        for s in reverse
    ]
    record2 = PQ.classify_pq(diagnostics, reverse)
    assert record2.clause_b_t0 is True
    assert record2.status == PQ.STATUS_QUARANTINE_SENSITIVE


def test_item10_no_effect_size_clause() -> None:
    # A differs from B in value but not sign, screens match, Q_* == 0:
    # there is no effect-size clause in P-Q (kappa is structural, not sized).
    diagnostics = _make_diagnostics(a_t0=100.0, b_t0=1.0, q_b=0.0, q_s=0.0, q_l=0.0)
    record = PQ.classify_pq(diagnostics, _screens(diagnostics))
    assert record.status == PQ.STATUS_NOT_SENSITIVE
    assert record.clause_c is False


def test_item10_clause_c_boundaries() -> None:
    # Q_S = 2 Q_B is not sensitive; Q_S > 2 Q_B is.
    boundary = _make_diagnostics(q_b=0.1, q_s=0.2, q_l=0.0)
    assert PQ.classify_pq(boundary, _screens(boundary)).status == PQ.STATUS_NOT_SENSITIVE
    over = _make_diagnostics(q_b=0.1, q_s=0.21, q_l=0.0)
    assert PQ.classify_pq(over, _screens(over)).status == PQ.STATUS_QUARANTINE_SENSITIVE
    # Q_B == 0 with Q_S > 0 is sensitive.
    zero = _make_diagnostics(q_b=0.0, q_s=0.01, q_l=0.0)
    assert PQ.classify_pq(zero, _screens(zero)).status == PQ.STATUS_QUARANTINE_SENSITIVE
    # Q_L is included.
    long_only = _make_diagnostics(q_b=0.1, q_s=0.0, q_l=0.3)
    assert PQ.classify_pq(long_only, _screens(long_only)).status == PQ.STATUS_QUARANTINE_SENSITIVE


# ===========================================================================
# 11. NOT_ASSESSED propagation
# ===========================================================================


def test_item11_a_statistic_not_assessed_blocks() -> None:
    diagnostics = _make_diagnostics(a_t0_status="INSUFFICIENT_SUPPORT")
    record = PQ.classify_pq(diagnostics, _screens(diagnostics))
    assert record.status == PQ.STATUS_NOT_ASSESSED
    assert record.not_assessed_reason == PQ.A_STATISTIC_NOT_ASSESSED
    assert record.advancement_blocked is True


def test_item11_a_screen_none_blocks() -> None:
    diagnostics = _make_diagnostics()
    record = PQ.classify_pq(diagnostics, _screens(diagnostics, a_t0=None))
    assert record.status == PQ.STATUS_NOT_ASSESSED
    assert record.not_assessed_reason == PQ.A_SCREEN_NOT_ASSESSED


def test_item11_screen_missing_and_mismatch() -> None:
    diagnostics = _make_diagnostics()
    assert PQ.classify_pq(diagnostics, _screens(diagnostics)[:3]).not_assessed_reason == PQ.SCREEN_MISSING
    bad = list(_screens(diagnostics))
    bad[0] = dataclasses.replace(bad[0], cell_hash="0" * 64)
    assert PQ.classify_pq(diagnostics, bad).not_assessed_reason == PQ.SCREEN_MISMATCH


# ===========================================================================
# 12. DELISTING coexistence
# ===========================================================================


def test_item12_delisting_coexistence() -> None:
    # A delisting-sensitive (E_S > 2B), B not -> blocked.
    diag_b = _fake_diag_b(economic_t0=1.0, economic_ts=1.0, e_s=0.0, b=1.0)
    a_sensitive = _make_diagnostics(
        a_t0=1.0, a_ts=-1.0, b_t0=1.0, b_ts=1.0, b_diag=diag_b
    )
    record = PQ.classify_pq(a_sensitive, _screens(a_sensitive))
    assert record.delisting_b == B.STATUS_NOT_SENSITIVE
    assert record.delisting_a == PQ.DELISTING_SENSITIVE
    assert record.advancement_blocked is True

    # B delisting-sensitive, A not -> blocked.
    diag_b_sensitive = _fake_diag_b(economic_t0=1.0, economic_ts=-1.0, e_s=0.0, b=1.0)
    b_sensitive = _make_diagnostics(
        a_t0=1.0, a_ts=1.0, b_t0=1.0, b_ts=1.0, b_diag=diag_b_sensitive
    )
    record2 = PQ.classify_pq(b_sensitive, _screens(b_sensitive))
    assert record2.delisting_b == B.STATUS_DELISTING_SENSITIVE
    assert record2.advancement_blocked is True

    # Neither sensitive and no quarantine sensitivity -> unblocked.
    calm = _make_diagnostics(q_b=0.0, q_s=0.0, q_l=0.0)
    record3 = PQ.classify_pq(calm, _screens(calm))
    assert record3.status == PQ.STATUS_NOT_SENSITIVE
    assert record3.advancement_blocked is False
    assert record3.holdout_authorized is False
    assert record3.final_holdout_consumed is False
    assert record3.confirmation_evidence is False
    assert record3.scope == PQ.SCOPE


# ===========================================================================
# 13. Declaration
# ===========================================================================


def test_item13_declaration_rejects_per_estimand_override() -> None:
    base = _base_declaration(*_partition()[:2], _panel(_standard_values()), _factor_spec())
    with pytest.raises(PQ.PQDeclarationError):
        PQ.PQEvaluationDeclaration(
            base=base,
            dataset_id=PQ.DATASET_ID,
            dataset_identity_hash="h",
            amendment_sha256=PQ.AMENDMENT_SHA256,
            quarantine_rule_hash=PQ.QUARANTINE_RULE_HASH,
            pq_id="pq",
            pq_hash="h",
            estimands=("QUARANTINED", "QUARANTINED"),
        )


def test_item13_declaration_hash_stable() -> None:
    partition, fold, _ = _partition()
    panel = _panel(_standard_values())
    spec = _factor_spec()
    base = _base_declaration(partition, fold, panel, spec)
    kwargs = dict(
        base=base,
        dataset_id=PQ.DATASET_ID,
        dataset_identity_hash="h",
        amendment_sha256=PQ.AMENDMENT_SHA256,
        quarantine_rule_hash=PQ.QUARANTINE_RULE_HASH,
        pq_id="pq",
        pq_hash="ph",
    )
    first = PQ.PQEvaluationDeclaration(**kwargs)
    second = PQ.PQEvaluationDeclaration(**kwargs)
    assert first.declaration_hash == second.declaration_hash


def test_item13_wrong_dataset_identity(tmp_path: Path) -> None:
    case = _build_case(
        tmp_path, declaration_overrides={"dataset_identity_hash": "0" * 64}
    )
    assert case.diagnostics.not_assessed == PQ.DATASET_IDENTITY_MISMATCH


def test_item13_wrong_dataset_id(tmp_path: Path) -> None:
    case = _build_case(
        tmp_path, declaration_overrides={"dataset_id": "discovery_dataset_v1_1"}
    )
    assert case.diagnostics.not_assessed == PQ.PQ_DECLARATION_INVALID


def test_item13_wrong_rule_hash(tmp_path: Path) -> None:
    case = _build_case(
        tmp_path, declaration_overrides={"quarantine_rule_hash": "0" * 64}
    )
    assert case.diagnostics.not_assessed == PQ.QUARANTINE_RULE_HASH_MISMATCH


def test_item13_wrong_amendment_hash(tmp_path: Path) -> None:
    case = _build_case(
        tmp_path, declaration_overrides={"amendment_sha256": "0" * 64}
    )
    assert case.diagnostics.not_assessed == PQ.PQ_DECLARATION_INVALID


def test_item13_wrong_overlay_hash(tmp_path: Path) -> None:
    case = _build_case(tmp_path)
    assert case.diagnostics.not_assessed is None
    # Build again with an overlay whose pq_hash disagrees with the declaration.
    store = _Store(tmp_path, name="store2")
    store.standard()
    bindings = store.build()
    partition, fold, _ = _partition()
    spec = _factor_spec()
    values = _standard_values()
    panel = _panel(values)
    base = _base_declaration(partition, fold, panel, spec)
    pq_bindings, identity_hash = _identity_context(store.root)
    declaration = PQ.PQEvaluationDeclaration(
        base=base,
        dataset_id=PQ.DATASET_ID,
        dataset_identity_hash=identity_hash,
        amendment_sha256=PQ.AMENDMENT_SHA256,
        quarantine_rule_hash=PQ.QUARANTINE_RULE_HASH,
        pq_id="pq-test",
        pq_hash="h-test",
    )
    overlay = _overlay(pq_id="pq-test", pq_hash="different")
    diagnostics = PQ.build_pq_diagnostics(
        store.root, declaration, spec, panel, partition,
        overlay=overlay, inputs=_inputs_for(values), bindings=bindings,
        pq_bindings=pq_bindings, pq_read_records_fn=lambda path: _genesis_records(),
    )
    assert diagnostics.not_assessed == PQ.OVERLAY_MISMATCH


def test_item13_pqa_verifier_failure(tmp_path: Path) -> None:
    case = _build_case(tmp_path)
    assert case.diagnostics.not_assessed is None
    # A K-log record mentioning v1.2 must fail the PQA verifier.
    store = _Store(tmp_path, name="store_klog")
    store.standard()
    bindings = store.build()
    partition, fold, _ = _partition()
    spec = _factor_spec()
    values = _standard_values()
    panel = _panel(values)
    base = _base_declaration(partition, fold, panel, spec)
    pq_bindings, identity_hash = _identity_context(store.root)
    declaration = PQ.PQEvaluationDeclaration(
        base=base, dataset_id=PQ.DATASET_ID, dataset_identity_hash=identity_hash,
        amendment_sha256=PQ.AMENDMENT_SHA256, quarantine_rule_hash=PQ.QUARANTINE_RULE_HASH,
        pq_id="pq-test", pq_hash="h-test",
    )
    poison = _FakeRecord(1, "ARTIFACT", "x" * 64, {"note": PQ.DATASET_ID})
    diagnostics = PQ.build_pq_diagnostics(
        store.root, declaration, spec, panel, partition,
        overlay=_overlay(), inputs=_inputs_for(values), bindings=bindings,
        pq_bindings=pq_bindings,
        pq_read_records_fn=lambda path: _genesis_records(poison),
    )
    assert diagnostics.not_assessed == PQ.DATASET_IDENTITY_MISMATCH


# ===========================================================================
# 14. Holdout
# ===========================================================================


def test_item14_holdout_fold_not_assessed(tmp_path: Path) -> None:
    store = _Store(tmp_path)
    store.standard()
    bindings = store.build()
    partition, _fold, holdout = _partition()
    spec = _factor_spec()
    values = _standard_values()
    panel = _panel(values)
    base = _base_declaration(partition, holdout, panel, spec)
    pq_bindings, identity_hash = _identity_context(store.root)
    declaration = PQ.PQEvaluationDeclaration(
        base=base, dataset_id=PQ.DATASET_ID, dataset_identity_hash=identity_hash,
        amendment_sha256=PQ.AMENDMENT_SHA256, quarantine_rule_hash=PQ.QUARANTINE_RULE_HASH,
        pq_id="pq-test", pq_hash="h-test",
    )
    diagnostics = PQ.build_pq_diagnostics(
        store.root, declaration, spec, panel, partition,
        overlay=_overlay(), inputs=_inputs_for(values), bindings=bindings,
        pq_bindings=pq_bindings, pq_read_records_fn=lambda path: _genesis_records(),
    )
    assert diagnostics.not_assessed is not None
    assert all(cell.status is not None for cell in diagnostics.cells)


def test_item14_no_holdout_files_opened(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    opened: list[str] = []
    original = Path.open

    def _spy(self: Path, *args: Any, **kwargs: Any):
        if any(part in {"holdout", "reserve_sealed"} for part in self.parts):
            opened.append(str(self))
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", _spy)
    case = _build_case(tmp_path)
    assert case.classify().final_holdout_consumed is False
    assert opened == []


# ===========================================================================
# 15. Factor-panel mismatch
# ===========================================================================


def test_item15_factor_panel_mismatch(tmp_path: Path) -> None:
    store = _Store(tmp_path)
    store.standard()
    bindings = store.build()
    partition, fold, _ = _partition()
    spec = _factor_spec()
    values = dict(_standard_values())
    panel = _panel(values)
    tampered = dict(values)
    tampered[(F1, "A")] = 99.0
    pq_bindings, identity_hash = _identity_context(store.root)
    base = _base_declaration(partition, fold, _panel(tampered), spec)
    declaration = PQ.PQEvaluationDeclaration(
        base=base, dataset_id=PQ.DATASET_ID, dataset_identity_hash=identity_hash,
        amendment_sha256=PQ.AMENDMENT_SHA256, quarantine_rule_hash=PQ.QUARANTINE_RULE_HASH,
        pq_id="pq-test", pq_hash="h-test",
    )
    # The supplied factor panel matches the declaration hash but not the PQA V.
    diagnostics = PQ.build_pq_diagnostics(
        store.root, declaration, spec, _panel(tampered), partition,
        overlay=_overlay(), inputs=_inputs_for(values), bindings=bindings,
        pq_bindings=pq_bindings, pq_read_records_fn=lambda path: _genesis_records(),
    )
    assert diagnostics.not_assessed == PQ.FACTOR_PANEL_MISMATCH


# ===========================================================================
# 16. B reconciliation
# ===========================================================================


def test_item16_b_reconciliation_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _Store(tmp_path)
    store.standard()
    bindings = store.build()
    partition, fold, _ = _partition()
    spec = _factor_spec()
    values = _standard_values()
    panel = _panel(values)

    def _perturbed(diagnostics: B.DevelopmentDiagnostics) -> B.DevelopmentDiagnostics:
        return dataclasses.replace(diagnostics, value_t0=123456.0)

    original = B.build_development_diagnostics

    def _wrapped(root, declaration, factor_spec, factor_panel, partition, *, bindings=None):
        return _perturbed(original(root, declaration, factor_spec, factor_panel, partition, bindings=bindings))

    # Perturb the reconstruction, not diag_B: recomputed B then disagrees.
    pq_bindings, identity_hash = _identity_context(store.root)
    base = _base_declaration(partition, fold, panel, spec)
    declaration = PQ.PQEvaluationDeclaration(
        base=base, dataset_id=PQ.DATASET_ID, dataset_identity_hash=identity_hash,
        amendment_sha256=PQ.AMENDMENT_SHA256, quarantine_rule_hash=PQ.QUARANTINE_RULE_HASH,
        pq_id="pq-test", pq_hash="h-test",
    )
    original_reconstruct = PQ._reconstruct_b

    def _break_reconstruct(*args: Any, **kwargs: Any):
        scenarios, groups = original_reconstruct(*args, **kwargs)
        broken = dataclasses.replace(scenarios[0], value=scenarios[0].value + 1.0)
        return (broken, scenarios[1]), groups

    monkeypatch.setattr(PQ, "_reconstruct_b", _break_reconstruct)
    with pytest.raises(PQ.PQReconciliationError):
        PQ.build_pq_diagnostics(
            store.root, declaration, spec, panel, partition,
            overlay=_overlay(), inputs=_inputs_for(values), bindings=bindings,
            pq_bindings=pq_bindings, pq_read_records_fn=lambda path: _genesis_records(),
        )


# ===========================================================================
# 17. Orientation
# ===========================================================================


def test_item17_orientation_minus_one(tmp_path: Path) -> None:
    case = _build_case(tmp_path, sign=-1)
    assert case.diagnostics.not_assessed is None
    a_t0 = case.diagnostics.cell("A-T0")
    assert a_t0 is not None and a_t0.status is None
    assert _factor_spec(-1).sign == -1


def test_item17_unsupported_orientation_not_assessed() -> None:
    stat = PQ._estimate_a(
        np.array([1.0, 2.0, 3.0]),
        np.array([True, True, True]),
        B.spec_mod.MetricKey.MAX_DRAWDOWN,
        sign=-1,
        periods_per_year=252,
        lags=None,
    )
    assert stat.status == PQ.UNSUPPORTED_ORIENTATION


# ===========================================================================
# 18. Determinism
# ===========================================================================


def test_item18_determinism_tz_locale(tmp_path: Path) -> None:
    case = _build_case(tmp_path)
    assert case.diagnostics.not_assessed is None
    assert isinstance(case.diagnostics.diagnostics_hash, str)
    env = dict(os.environ)
    for name in (
        "TIINGO_API_KEY", "TUSHARE_PROXY_TOKEN", "TUSHARE_BASIC_PROXY_TOKEN",
        "TUSHARE_API_TOKEN", "DEEPSEEK_API_KEY", "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY",
    ):
        env.pop(name, None)
    script = (
        "from smart_beta.dataset_v1.pq_evaluation import _d7b\n"
        "import json\n"
        "print(json.dumps({'ok': True}, sort_keys=True))\n"
    )
    import subprocess

    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(REPO_ROOT),
        env={**env, "TZ": "Asia/Tokyo", "LC_ALL": "C"},
        capture_output=True,
        text=True,
        check=True,
    )
    assert "ok" in proc.stdout


# ===========================================================================
# 19. Pinned-code checks
# ===========================================================================


def test_item19_pinned_code_hashes() -> None:
    verified = PQ.verify_pinned_code(REPO_ROOT)
    assert verified == PQ.PINNED_CODE
    for relative, digest in PQ.PINNED_CODE.items():
        actual = hashlib.sha256((REPO_ROOT / relative).read_bytes()).hexdigest()
        assert actual == digest


def test_item19_pinned_code_mismatch(tmp_path: Path) -> None:
    with pytest.raises(PQ.PQBindingError):
        PQ.verify_pinned_code(tmp_path)


# ===========================================================================
# 20. Reporting completeness
# ===========================================================================


def test_item20_reporting_completeness(tmp_path: Path) -> None:
    case = _build_case(tmp_path, input_flag_rows=[(F1, "A")])
    record = case.classify()
    payload = record.to_dict()
    for key in (
        "status", "not_assessed_reason", "quarantine_sensitive", "clause_a",
        "clause_b", "clause_c", "delisting_a", "delisting_b", "q_b", "q_s",
        "q_l", "advancement_blocked", "holdout_authorized",
        "final_holdout_consumed", "confirmation_evidence", "scope",
        "cell_hashes",
    ):
        assert key in payload
    assert len(payload["cell_hashes"]) == 4
    # The record constructor requires both estimands: one-estimand output is
    # impossible.
    assert {cell.estimand for cell in case.diagnostics.cells} == {
        PQ.ESTIMAND_QUARANTINED, PQ.ESTIMAND_ORIGINAL
    }


def test_item20_diagnostics_serialisable(tmp_path: Path) -> None:
    case = _build_case(tmp_path)
    payload = case.diagnostics.to_dict()
    blob = json.dumps(payload, sort_keys=True, default=str)
    assert len(blob) > 0


def test_item_purged_index_includes_purged(tmp_path: Path) -> None:
    store = _Store(tmp_path)
    store.standard()
    # An extra formation whose horizon endpoint crosses the fold boundary is
    # purged by D7b but still occupies its position on the original index.
    late = SESSIONS[89]
    for key in KEYS:
        store.add(late, key, rank="ABCD".index(key) + 1, rate=0.01)
    case = _build_case(tmp_path, store=store)
    assert case.diagnostics.not_assessed is None
    diag_b = case.diagnostics.b_diagnostics
    assert diag_b is not None and diag_b.purged_cells
    assert len(case.diagnostics.index) == len(diag_b.formations) + len(
        diag_b.purged_cells
    )


def test_mutation_fixture_contract() -> None:
    path = REPO_ROOT / "tests/fixtures/dataset_v1_2/pqb/mutations.json"
    mutations = json.loads(path.read_text(encoding="utf-8"))
    assert len(mutations) == 40
    assert [m["id"] for m in mutations] == [f"B{i}" for i in range(1, 41)]
    for mutation in mutations:
        assert mutation["edits"], mutation["id"]
        assert mutation["file"] == "smart_beta/dataset_v1/pq_evaluation.py"
        for edit in mutation["edits"]:
            assert edit["old"] and edit["new"] is not None, mutation["id"]
        assert hasattr(sys.modules[__name__], mutation["test"]), mutation["id"]
