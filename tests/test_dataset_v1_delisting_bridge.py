"""D7b development delisting-sensitivity bridge tests.

Every test builds a synthetic ``tmp_path`` D7a store with injected
:class:`~smart_beta.dataset_v1.delisting_bridge.Bindings`; none touches the
real ``data_store``, ``reserve_sealed`` or any credential.  An autouse socket
guard and credential sentinel mirror the D7a/D6/D5 suites.

The test functions are named ``test_itemNNN_*`` so the report's test-to-item
mapping (items 1-133 of the frozen spec §7) is mechanical.

Each test is independent, fast and deterministic.  Mutation verification (the
planner re-runs the shipped ``tests/fixtures/dataset_v1/d7b/mutations.json``)
relies on at least one of these tests failing per mutation.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import json
import math
import os
import socket
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd
import pytest

import smart_beta.spec.requirements as R
from smart_beta.evaluation.partition import (
    Fold,
    FoldRole,
    HoldoutAlreadyConsumedError,
    HoldoutRegistry,
    Partition,
)
from smart_beta.evaluation import partition as partition_mod
from smart_beta.spec.factor_spec import FactorInput, FactorSpec, MissingPolicy
from smart_beta.dataset_v1 import delisting_bridge as B


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------

CREDENTIAL_SENTINEL = "d7b-credential-sentinel-5f3a"


@pytest.fixture(autouse=True)
def _no_sockets(monkeypatch: pytest.MonkeyPatch):
    def _refuse(*args: object, **kwargs: object) -> None:
        raise RuntimeError("D7b tests must never open a socket")

    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(socket, "create_connection", _refuse)
    yield


@pytest.fixture(autouse=True)
def _credential_sentinel(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TIINGO_API_KEY", CREDENTIAL_SENTINEL)
    yield


# ---------------------------------------------------------------------------
# Synthetic calendar / fold
# ---------------------------------------------------------------------------


def _business_days(start: dt.date, n: int) -> list[dt.date]:
    out: list[dt.date] = []
    d = start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += dt.timedelta(days=1)
    return out


SESSIONS = _business_days(dt.date(2019, 12, 2), 200)
POS = {d: i for i, d in enumerate(SESSIONS)}
F1 = SESSIONS[0]
F2 = SESSIONS[4]
FOLD_START = SESSIONS[0]
FOLD_END = SESSIONS[90]
HOLDOUT_START = SESSIONS[90]
HOLDOUT_END = SESSIONS[130]
HORIZON = 3
ARTIFACT_ID = "r-d7btest00000000"

DEFAULT_RATES = {"A": 0.01, "B": 0.02, "C": 0.03, "D": 0.04}


def _compound(rate: float, n: int) -> float:
    return (1.0 + rate) ** n - 1.0


def _future(f: dt.date, n: int) -> dt.date:
    return SESSIONS[POS[f] + n]


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class _Member:
    formation: dt.date
    key: str
    state: str = "COMPLETE"
    affected: bool = False
    m: int | None = None
    flag: str = "TOP_1000"
    rank: int = 1
    rate: float = 0.01
    daily: list[tuple[dt.date, str, float]] | None = None
    observed_segment_end: dt.date | None = None
    endpoint: dt.date | None = None
    listed_end_dev: dt.date | None = None
    top_200: bool | None = None
    top_500: bool | None = None
    top_1000: bool | None = None
    n_invalid_sessions: int = 0
    first_invalid_session: dt.date | None = None
    first_invalid_status: str | None = None


class _Store:
    """A synthetic D7a artifact store with injected bindings."""

    def __init__(self, tmp_path: Path, name: str = "store", horizon: int = HORIZON):
        self.root = tmp_path / name
        self.root.mkdir(parents=True, exist_ok=True)
        self.horizon = horizon
        self.members: list[_Member] = []
        self.daily: list[tuple[str, dt.date, str, float]] = []
        self.extra_daily: list[tuple[str, dt.date, str, float]] = []
        self.crosses_reserve: dict[dt.date, bool] = {}
        self.endpoint_override: dict[dt.date, dt.date] = {}
        self.horizon_override: dict[dt.date, int] = {}
        self.explicit_daily = False

    def add(self, formation: dt.date, key: str, **kwargs: Any) -> _Member:
        member = _Member(formation=formation, key=key, **kwargs)
        if member.state == "EMPTY":
            member.affected = True
        self.members.append(member)
        return member

    def standard(
        self,
        formations: Sequence[dt.date] = (F1, F2),
        *,
        terminal: str | None = None,
        rates: Mapping[str, float] | None = None,
    ) -> None:
        rates = dict(DEFAULT_RATES if rates is None else rates)
        for f in formations:
            for key in ("A", "B", "C", "D"):
                kw: dict[str, Any] = {
                    "rank": "ABCD".index(key) + 1,
                    "rate": rates[key],
                }
                if terminal is not None and key == terminal:
                    kw.update(state="COMPLETE", affected=True, m=1)
                self.add(f, key, **kw)

    def _flattens(self) -> list[_Member]:
        return list(self.members)

    def build(self, *, reverse: bool = False) -> B.Bindings:
        root = self.root
        members_rows: list[dict[str, Any]] = []
        horizon_rows: list[dict[str, Any]] = []
        label_rows: list[dict[str, Any]] = []
        daily_rows: list[tuple[str, dt.date, str, float]] = []
        formations = sorted({m.formation for m in self.members})
        for f in formations:
            h = self.horizon_override.get(f, self.horizon)
            endpoint = self.endpoint_override.get(f, _future(f, h))
            horizon_rows.append(
                {
                    "formation_date": f,
                    "h": h,
                    "endpoint": endpoint,
                    "crosses_reserve": self.crosses_reserve.get(f, False),
                }
            )
        for m in self.members:
            h = self.horizon_override.get(m.formation, self.horizon)
            endpoint = m.endpoint or self.endpoint_override.get(
                m.formation, _future(m.formation, h)
            )
            if m.observed_segment_end is not None:
                segment_end = m.observed_segment_end
            elif m.affected and m.m is not None:
                segment_end = _future(m.formation, m.m)
            else:
                segment_end = endpoint
            sessions = m.m if m.m is not None else h
            members_rows.append(
                {
                    "formation_date": m.formation,
                    "security_key": m.key,
                    "ticker": m.key,
                    "rank": m.rank,
                    "top_200": (
                        m.top_200 if m.top_200 is not None else m.flag == "TOP_200"
                    ),
                    "top_500": (
                        m.top_500
                        if m.top_500 is not None
                        else m.flag in ("TOP_200", "TOP_500")
                    ),
                    "top_1000": (
                        m.top_1000 if m.top_1000 is not None else True
                    ),
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
                    "h": h,
                    "endpoint": endpoint,
                    "label_state": label_state,
                    "terminal_affected": m.affected,
                    "observed_path_complete": label_state == "COMPLETE",
                    "n_observed_sessions": sessions,
                    "n_post_terminal_sessions": 0,
                    "n_invalid_sessions": m.n_invalid_sessions,
                    "observed_segment_end": segment_end,
                    "first_invalid_session": m.first_invalid_session,
                    "first_invalid_status": m.first_invalid_status,
                }
            )
            if m.daily is not None:
                daily_rows.extend((m.key, d, s, r) for d, s, r in m.daily)
            elif m.state != "EMPTY":
                cover = m.m if (m.affected and m.m is not None) else sessions
                for j in range(1, cover + 1):
                    daily_rows.append(
                        (m.key, _future(m.formation, j), "VALID", m.rate)
                    )
        daily_rows.extend(self.extra_daily)
        if reverse:
            members_rows.reverse()
            horizon_rows.reverse()
            label_rows.reverse()
            daily_rows.reverse()

        daily = pd.DataFrame(
            daily_rows,
            columns=[
                "security_key",
                "date",
                "session_status",
                "daily_total_return",
            ],
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


def _panel(
    values: Mapping[tuple[dt.date, str], float],
) -> pd.DataFrame:
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
    formations: Sequence[dt.date] = (F1, F2),
    keys: Sequence[str] = ("A", "B", "C", "D"),
) -> dict[tuple[dt.date, str], float]:
    values: dict[tuple[dt.date, str], float] = {}
    for f in formations:
        for i, key in enumerate(keys):
            values[(f, key)] = float(i + 1)
    return values


def _partition() -> tuple[Partition, Fold, Fold]:
    fold = Fold(FoldRole.IS, FOLD_START, FOLD_END)
    holdout = Fold(FoldRole.HOLDOUT, HOLDOUT_START, HOLDOUT_END)
    return Partition(folds=(fold, holdout), split_rule="d7b-test"), fold, holdout


def _spec(sign: int = 1) -> FactorSpec:
    requirement = R.DataRequirement(
        semantic_id="turnover",
        frequency=R.Frequency.DAILY,
        observation_period=R.ObservationPeriod.PERIOD,
        units=R.Unit.RATIO,
        lookback=20,
        revision_policy=R.RevisionPolicy.POINT_IN_TIME,
    )
    return FactorSpec(
        id="d7b_factor",
        description="synthetic d7b factor",
        expression="mean(turnover, 20)",
        inputs=(FactorInput(alias="turnover", requirement=requirement),),
        frequency=R.Frequency.DAILY,
        missing_policy=MissingPolicy.PROPAGATE,
        sign=sign,
    )


def _declaration(
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


@dataclasses.dataclass
class _Case:
    root: Path
    store: _Store
    bindings: B.Bindings
    partition: Partition
    fold: Fold
    panel: pd.DataFrame
    factor_spec: FactorSpec
    declaration: B.CampaignEvaluationDeclaration
    diagnostics: B.DevelopmentDiagnostics

    def screens(
        self, t0: bool = True, ts: bool = False
    ) -> tuple[B.ScreenOutcome, B.ScreenOutcome]:
        return (
            B.ScreenOutcome(
                self.declaration.screen_id,
                self.declaration.screen_policy_hash,
                self.declaration.declaration_hash,
                self.diagnostics.diagnostics_hash,
                B.SCENARIO_T0,
                t0,
            ),
            B.ScreenOutcome(
                self.declaration.screen_id,
                self.declaration.screen_policy_hash,
                self.declaration.declaration_hash,
                self.diagnostics.diagnostics_hash,
                B.SCENARIO_TS,
                ts,
            ),
        )

    def classify(self, t0: bool = True, ts: bool = False):
        return B.classify_delisting_sensitivity(self.diagnostics, *self.screens(t0, ts))


def _run(
    store: _Store,
    *,
    panel: pd.DataFrame | None = None,
    sign: int = 1,
    partition: Partition | None = None,
    fold: Fold | None = None,
    spec: FactorSpec | None = None,
    declaration: B.CampaignEvaluationDeclaration | None = None,
    build: bool = True,
    **overrides: Any,
) -> _Case:
    if not store.members:
        store.standard()
    bindings = store.build()
    if partition is None or fold is None:
        partition, fold, _ = _partition()
    if panel is None:
        panel = _panel(_standard_values())
    if spec is None:
        spec = _spec(sign)
    if declaration is None:
        decl = _declaration(partition, fold, panel, spec, **overrides)
    else:
        decl = declaration
    diagnostics = (
        B.build_development_diagnostics(
            store.root, decl, spec, panel, partition, bindings=bindings
        )
        if build
        else None
    )
    return _Case(
        store.root, store, bindings, partition, fold, panel, spec, decl, diagnostics
    )


# ===========================================================================
# 1-8: bindings and inputs
# ===========================================================================


def test_item001_d7a_file_hash_mismatch_fails_closed(tmp_path: Path) -> None:
    store = _Store(tmp_path)
    store.standard()
    bindings = store.build()
    base = bindings.d7a.files
    for index in range(len(base)):
        tampered = list(base)
        original = tampered[index]
        tampered[index] = B.FileBinding(original.relative_path, "0" * 64)
        broken = dataclasses.replace(bindings, d7a=dataclasses.replace(
            bindings.d7a, files=tuple(tampered)
        ))
        with pytest.raises(B.D7bBindingError):
            B.build_development_diagnostics(
                store.root,
                _declaration(*_partition()[:2], _panel(_standard_values()), _spec()),
                _spec(),
                _panel(_standard_values()),
                _partition()[0],
                bindings=broken,
            )


def test_item002_returns_manifest_and_hash_mismatch_fail_closed(
    tmp_path: Path,
) -> None:
    store = _Store(tmp_path)
    store.standard()
    bindings = store.build()
    manifest_path = store.root / bindings.d7a.manifest_relative_path
    original = json.loads(manifest_path.read_text())
    # (a) manifest content tampered (file hash no longer matches binding)
    manifest_path.write_text(json.dumps({**original, "returns_hash": "0" * 64}))
    partition, fold, _ = _partition()
    panel = _panel(_standard_values())
    with pytest.raises(B.D7bBindingError):
        B.build_development_diagnostics(
            store.root,
            _declaration(partition, fold, panel, _spec()),
            _spec(),
            panel,
            partition,
            bindings=bindings,
        )
    # (b) binding returns_hash tampered
    manifest_path.write_text(json.dumps(original))
    broken = dataclasses.replace(
        bindings, d7a=dataclasses.replace(bindings.d7a, returns_hash="0" * 64)
    )
    with pytest.raises(B.D7bBindingError):
        B.build_development_diagnostics(
            store.root,
            _declaration(partition, fold, panel, _spec()),
            _spec(),
            panel,
            partition,
            bindings=broken,
        )


def test_item003_decoy_newer_d7a_directory_is_ignored(tmp_path: Path) -> None:
    store = _Store(tmp_path)
    store.standard()
    bindings = store.build()
    decoy = store.root / "derived" / "returns" / "r-9999999999999999"
    decoy.mkdir(parents=True, exist_ok=True)
    (decoy / "members.parquet").write_bytes(b"not a parquet file")
    case = _run(store, panel=_panel(_standard_values()))
    assert case.diagnostics.not_assessed is None


def test_item004_code_and_document_hash_mismatch_fail_closed(
    tmp_path: Path,
) -> None:
    store = _Store(tmp_path)
    store.standard()
    bindings = store.build()
    partition, fold, _ = _partition()
    panel = _panel(_standard_values())
    spec = _spec()
    declaration = _declaration(partition, fold, panel, spec)

    bad_code = list(bindings.code_sha256)
    bad_code[0] = (bad_code[0][0], "0" * 64)
    with pytest.raises(B.D7bBindingError):
        B.build_development_diagnostics(
            store.root, declaration, spec, panel, partition,
            bindings=dataclasses.replace(bindings, code_sha256=tuple(bad_code)),
        )

    bad_docs = list(bindings.documents)
    bad_docs[0] = dataclasses.replace(bad_docs[0], sha256="0" * 64)
    with pytest.raises(B.D7bBindingError):
        B.build_development_diagnostics(
            store.root, declaration, spec, panel, partition,
            bindings=dataclasses.replace(bindings, documents=tuple(bad_docs)),
        )


def test_item005_declaration_hash_is_deterministic_and_order_independent() -> None:
    first = B.CampaignEvaluationDeclaration(
        precommitment_ref="pc",
        factor_spec_version="v",
        factor_panel_sha256="a" * 64,
        partition_id="p",
        fold_key="f",
        universe="TOP_500",
        horizon=5,
        n_groups=3,
        winsorization=0.1,
        transaction_cost_bps=5.0,
        periods_per_year=252,
        weighting="EQUAL",
        primary_metric=B.spec_mod.MetricKey.IC,
        basis="NONE",
        screen_id="s",
        screen_policy_hash="ph",
    )
    second = B.CampaignEvaluationDeclaration(
        screen_policy_hash="ph",
        screen_id="s",
        basis="NONE",
        primary_metric=B.spec_mod.MetricKey.IC,
        weighting="EQUAL",
        periods_per_year=252,
        transaction_cost_bps=5.0,
        winsorization=0.1,
        n_groups=3,
        horizon=5,
        universe="TOP_500",
        fold_key="f",
        partition_id="p",
        factor_panel_sha256="a" * 64,
        factor_spec_version="v",
        precommitment_ref="pc",
    )
    assert first.declaration_hash == second.declaration_hash
    assert first.declaration_hash == B.content_hash(first.to_dict())


def test_item006_factor_panel_sha256_mismatch_raises(tmp_path: Path) -> None:
    case = _run(store=_Store(tmp_path))
    partition, fold, _ = _partition()
    panel = _panel(_standard_values())
    declaration = _declaration(
        partition, fold, panel, _spec(), factor_panel_sha256="0" * 64
    )
    with pytest.raises(B.D7bBindingError):
        B.build_development_diagnostics(
            case.root, declaration, _spec(), panel, partition,
            bindings=case.bindings,
        )


def test_item007_factor_spec_version_mismatch_raises(tmp_path: Path) -> None:
    store = _Store(tmp_path)
    store.standard()
    bindings = store.build()
    partition, fold, _ = _partition()
    panel = _panel(_standard_values())
    spec = _spec()
    declaration = _declaration(
        partition, fold, panel, spec, factor_spec_version="not-the-version"
    )
    with pytest.raises(B.D7bBindingError):
        B.build_development_diagnostics(
            store.root, declaration, spec, panel, partition, bindings=bindings
        )


def test_item008_partition_id_and_unknown_fold_key_raise(tmp_path: Path) -> None:
    store = _Store(tmp_path)
    store.standard()
    bindings = store.build()
    partition, fold, _ = _partition()
    panel = _panel(_standard_values())
    spec = _spec()
    bad_partition = _declaration(
        partition, fold, panel, spec, partition_id="0" * 64
    )
    with pytest.raises(B.D7bBindingError):
        B.build_development_diagnostics(
            store.root, bad_partition, spec, panel, partition, bindings=bindings
        )
    bad_fold = _declaration(partition, fold, panel, spec, fold_key="unknown")
    with pytest.raises(B.D7bBindingError):
        B.build_development_diagnostics(
            store.root, bad_fold, spec, panel, partition, bindings=bindings
        )


# ===========================================================================
# 9-28: scope and final holdout
# ===========================================================================


def test_item009_engine_evaluate_is_never_called(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from smart_beta.evaluation import engine

    calls = {"n": 0}

    def _spy(*args: object, **kwargs: object) -> None:
        calls["n"] += 1
        raise AssertionError("engine.evaluate must never be called by D7b")

    monkeypatch.setattr(engine, "evaluate", _spy)
    case = _run(_Store(tmp_path))
    assert calls["n"] == 0
    assert case.diagnostics.not_assessed is None


def test_item010_holdout_registry_is_never_constructed_or_consumed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    constructions = {"n": 0}

    def _counting_init(self: object) -> None:
        constructions["n"] += 1
        object.__setattr__(self, "_d7b", True)

    def _consume(self: object, target: object) -> None:
        raise AssertionError("D7b must never consume a holdout")

    monkeypatch.setattr(HoldoutRegistry, "__init__", _counting_init)
    monkeypatch.setattr(HoldoutRegistry, "consume", _consume)
    _run(_Store(tmp_path))
    assert constructions["n"] == 0


def test_item011_scope_and_constant_fields(tmp_path: Path) -> None:
    case = _run(_Store(tmp_path))
    assert case.diagnostics.to_dict()["formation_accounting"]
    record = case.classify()
    assert record.scope == B.SCOPE
    assert record.final_holdout_consumed is False
    assert record.confirmation_evidence is False


def test_item012_holdout_period_rows_are_never_materialized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _Store(tmp_path)
    store.standard()
    # A holdout-period member must never enter the evaluated fold.
    store.add(HOLDOUT_START, "Z", rank=9)
    # A holdout-dated daily row for a fold member must never be materialized.
    store.extra_daily.append(("A", HOLDOUT_START, "VALID", 0.01))
    seen: list[str] = []
    original = B._Resolver.read_parquet

    def _spy(self: B._Resolver, relative_path: str, **kwargs: Any) -> pd.DataFrame:
        assert kwargs.get("filters"), "every read must carry a row filter"
        frame = original(self, relative_path, **kwargs)
        for value in frame.get("formation_date", pd.Series(dtype=object)):
            seen.append(str(value))
        return frame

    monkeypatch.setattr(B._Resolver, "read_parquet", _spy)
    case = _run(store)
    assert case.diagnostics.not_assessed is None
    assert HOLDOUT_START not in case.diagnostics.formations
    assert not any(str(HOLDOUT_START) in value for value in seen)


def test_item013_holdout_role_fold_is_final_holdout_overlap(tmp_path: Path) -> None:
    store = _Store(tmp_path)
    store.standard()
    bindings = store.build()
    partition = Partition(
        folds=(
            Fold(FoldRole.IS, FOLD_START, FOLD_END),
            Fold(FoldRole.HOLDOUT, HOLDOUT_START, HOLDOUT_END),
        ),
        split_rule="d7b-test",
    )
    holdout = partition.holdout_fold
    assert holdout is not None
    panel = _panel(_standard_values())
    spec = _spec()
    declaration = _declaration(partition, holdout, panel, spec)
    diag = B.build_development_diagnostics(
        store.root, declaration, spec, panel, partition, bindings=bindings
    )
    assert diag.not_assessed == B.NOT_ASSESSED_FINAL_HOLDOUT_OVERLAP

    # A non-holdout fold that does not overlap the holdout but extends past
    # 2023-06-30 must be NOT_ASSESSED_NOT_DEVELOPMENT.
    late_fold = Fold(FoldRole.IS, dt.date(2023, 1, 2), dt.date(2024, 1, 2))
    late_holdout = Fold(
        FoldRole.HOLDOUT, dt.date(2024, 1, 2), dt.date(2024, 6, 3)
    )
    late_partition = Partition(
        folds=(late_fold, late_holdout), split_rule="late-development"
    )
    late_declaration = _declaration(late_partition, late_fold, panel, spec)
    late_diag = B.build_development_diagnostics(
        store.root,
        late_declaration,
        spec,
        panel,
        late_partition,
        bindings=bindings,
    )
    assert late_diag.not_assessed == B.NOT_ASSESSED_NOT_DEVELOPMENT
    record = B.classify_delisting_sensitivity(diag, None, None)
    assert record.status == B.STATUS_NOT_ASSESSED
    assert record.advancement_blocked


def test_item014_overlapping_holdout_fold_is_not_assessed(tmp_path: Path) -> None:
    store = _Store(tmp_path)
    store.standard()
    bindings = store.build()
    fold = Fold(FoldRole.IS, FOLD_START, FOLD_END)
    holdout = Fold(FoldRole.HOLDOUT, HOLDOUT_START, HOLDOUT_END)
    base = Partition(folds=(fold, holdout), split_rule="d7b-test")
    overlapping = Fold(FoldRole.HOLDOUT, FOLD_START, SESSIONS[40])

    class _Overlap(Partition):
        @property
        def holdout_fold(self) -> Fold:  # type: ignore[override]
            return overlapping

    partition = _Overlap(folds=base.folds, split_rule=base.split_rule)
    panel = _panel(_standard_values())
    spec = _spec()
    declaration = _declaration(partition, fold, panel, spec)
    diag = B.build_development_diagnostics(
        store.root, declaration, spec, panel, partition, bindings=bindings
    )
    assert diag.not_assessed == B.NOT_ASSESSED_FINAL_HOLDOUT_OVERLAP

    # A non-holdout fold that does not overlap the holdout but extends past
    # 2023-06-30 must be NOT_ASSESSED_NOT_DEVELOPMENT.
    late_fold = Fold(FoldRole.IS, dt.date(2023, 1, 2), dt.date(2024, 1, 2))
    late_holdout = Fold(
        FoldRole.HOLDOUT, dt.date(2024, 1, 2), dt.date(2024, 6, 3)
    )
    late_partition = Partition(
        folds=(late_fold, late_holdout), split_rule="late-development"
    )
    late_declaration = _declaration(late_partition, late_fold, panel, spec)
    late_diag = B.build_development_diagnostics(
        store.root,
        late_declaration,
        spec,
        panel,
        late_partition,
        bindings=bindings,
    )
    assert late_diag.not_assessed == B.NOT_ASSESSED_NOT_DEVELOPMENT


def test_item015_t0_diagnostic_consumes_zero_holdouts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    consumed = {"n": 0}
    monkeypatch.setattr(
        HoldoutRegistry, "consume",
        lambda self, target: consumed.__setitem__("n", consumed["n"] + 1),
    )
    case = _run(_Store(tmp_path))
    assert consumed["n"] == 0
    assert case.diagnostics.not_assessed is None


def test_item016_ts_diagnostic_consumes_zero_holdouts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    consumed = {"n": 0}
    monkeypatch.setattr(
        HoldoutRegistry, "consume",
        lambda self, target: consumed.__setitem__("n", consumed["n"] + 1),
    )
    store = _Store(tmp_path)
    store.standard(terminal="A")
    case = _run(store)
    assert consumed["n"] == 0
    assert case.diagnostics.value_ts is not None


def test_item017_t0_and_ts_together_consume_zero_holdouts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    consumed = {"n": 0}
    monkeypatch.setattr(
        HoldoutRegistry, "consume",
        lambda self, target: consumed.__setitem__("n", consumed["n"] + 1),
    )
    store = _Store(tmp_path)
    store.standard(terminal="A")
    case = _run(store)
    assert consumed["n"] == 0
    assert case.diagnostics.value_t0 is not None
    assert case.diagnostics.value_ts is not None


def test_item018_module_source_has_no_holdout_registry() -> None:
    source = Path(B.__file__).read_text()
    assert "HoldoutRegistry" not in source
    assert "consume(" not in source


def test_item019_no_phase_8_9_10_import_or_scenario_companion() -> None:
    import ast

    tree = ast.parse(Path(B.__file__).read_text())
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    for module in imported:
        assert "phase8" not in module
        assert "phase9" not in module
        assert "phase10" not in module
    source = Path(B.__file__).read_text()
    assert "scenario_companion" not in source
    assert "scenario companion" not in source


def test_item020_outputs_are_not_evaluation_or_decision_records(
    tmp_path: Path,
) -> None:
    from smart_beta.evaluation import spec as spec_mod

    case = _run(_Store(tmp_path))
    assert not isinstance(case.diagnostics, spec_mod.EvaluationRecord)
    assert type(case.diagnostics).__name__ != "EvaluationRecord"
    assert type(case.diagnostics).__name__ != "DecisionRecord"


def test_item021_confirmation_evidence_cannot_be_produced(tmp_path: Path) -> None:
    import ast

    tree = ast.parse(Path(B.__file__).read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert "phase10" not in alias.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            assert "phase10" not in node.module
    record = _run(_Store(tmp_path)).classify()
    assert record.confirmation_evidence is False
    assert record.to_dict()["confirmation_evidence"] is False


def test_item022_delisting_sensitive_blocks_advancement(tmp_path: Path) -> None:
    store = _Store(tmp_path)
    store.standard(terminal="A")
    case = _run(store)
    record = case.classify(t0=True, ts=False)
    assert record.status == B.STATUS_DELISTING_SENSITIVE
    assert record.advancement_blocked is True


def test_item023_not_assessed_blocks_advancement(tmp_path: Path) -> None:
    case = _run(_Store(tmp_path), primary_metric=B.spec_mod.MetricKey.BENCHMARK_RELATIVE,
                basis="NONE")
    record = case.classify()
    assert record.status == B.STATUS_NOT_ASSESSED
    assert record.advancement_blocked is True


def test_item024_not_sensitive_does_not_authorize_holdout(tmp_path: Path) -> None:
    case = _run(_Store(tmp_path))
    record = case.classify(t0=True, ts=True)
    assert record.status == B.STATUS_NOT_SENSITIVE
    assert record.advancement_blocked is False
    assert record.holdout_authorized is False


def test_item025_governance_unchanged_after_d7b(tmp_path: Path) -> None:
    store = _Store(tmp_path)
    store.standard()
    bindings = store.build()
    partition, fold, _ = _partition()
    panel = _panel(_standard_values())
    spec = _spec()
    declaration = _declaration(partition, fold, panel, spec)
    B.build_development_diagnostics(
        store.root, declaration, spec, panel, partition, bindings=bindings
    )
    registry = HoldoutRegistry()
    registry.consume(partition)
    with pytest.raises(HoldoutAlreadyConsumedError):
        registry.consume(partition)


def test_item026_t0_and_ts_use_identical_fold_and_formations(tmp_path: Path) -> None:
    store = _Store(tmp_path)
    store.standard(terminal="A")
    case = _run(store)
    record_groups = {
        record.formation_date
        for record in case.diagnostics.accounting_records
    }
    assert record_groups == set(case.diagnostics.formations)


def test_item027_t0_and_ts_share_membership_admissibility_grouping(
    tmp_path: Path,
) -> None:
    store = _Store(tmp_path)
    store.standard(terminal="A")
    case = _run(store)
    by_scenario: dict[str, dict[tuple[dt.date, str], tuple[Any, ...]]] = {}
    for record in case.diagnostics.accounting_records:
        by_scenario.setdefault(record.scenario, {})[
            (record.formation_date, record.stock_id)
        ] = (record.group, record.admissibility, record.expected_admissible)
    assert set(by_scenario) == {B.SCENARIO_T0, B.SCENARIO_TS}
    assert by_scenario[B.SCENARIO_T0] == by_scenario[B.SCENARIO_TS]


def test_item028_reserve_and_holdout_firewalls_are_distinct(tmp_path: Path) -> None:
    partition, fold, _ = _partition()
    panel = _panel(_standard_values())
    spec = _spec()
    # Reserve-only: a label carries a reserve date.
    store_r = _Store(tmp_path, name="reserve")
    store_r.standard()
    store_r.members[0].observed_segment_end = B.RESERVE_START
    bindings_r = store_r.build()
    declaration_r = _declaration(partition, fold, panel, spec)
    with pytest.raises(B.ReserveFirewallError):
        B.build_development_diagnostics(
            store_r.root, declaration_r, spec, panel, partition, bindings=bindings_r
        )
    # Holdout-only: a label carries a fold-boundary date before the reserve.
    store_h = _Store(tmp_path, name="holdout")
    store_h.standard()
    store_h.members[0].observed_segment_end = FOLD_END
    bindings_h = store_h.build()
    declaration_h = _declaration(partition, fold, panel, spec)
    with pytest.raises(B.FinalHoldoutFirewallError) as holdout_exc:
        B.build_development_diagnostics(
            store_h.root, declaration_h, spec, panel, partition, bindings=bindings_h
        )
    assert not isinstance(holdout_exc.value, B.ReserveFirewallError)


# ===========================================================================
# 29-32: purge and parity
# ===========================================================================


def test_item029_diagnostic_equals_engine_glue(tmp_path: Path) -> None:
    from smart_beta.evaluation import engine

    captured: list[tuple[pd.DataFrame, Any]] = []
    original = engine._metric_observations

    def _capture(panel: pd.DataFrame, portfolio: Any, *args: Any, **kwargs: Any):
        captured.append((panel.copy(), portfolio))
        return original(panel, portfolio, *args, **kwargs)

    store = _Store(tmp_path)
    store.standard(terminal="A")
    bindings = store.build()
    partition, fold, _ = _partition()
    panel = _panel(_standard_values())
    spec = _spec()
    declaration = _declaration(partition, fold, panel, spec)
    engine._metric_observations = _capture  # type: ignore[assignment]
    try:
        diagnostics = B.build_development_diagnostics(
            store.root, declaration, spec, panel, partition, bindings=bindings
        )
    finally:
        engine._metric_observations = original  # type: ignore[assignment]
    assert len(captured) == 2
    from smart_beta.evaluation.spec import MetricKey

    for expected, (captured_panel, portfolio) in zip(
        (diagnostics.value_t0, diagnostics.value_ts), captured
    ):
        _, metric_value, _ = original(
            captured_panel, portfolio, None, 252, (MetricKey.LONG_SHORT,)
        )[0]
        assert metric_value.value == expected



def test_item030_fold_boundary_cell_is_purged_before_p7b(tmp_path: Path) -> None:
    store = _Store(tmp_path)
    store.add(F1, "A", rank=1)
    store.add(F1, "B", rank=2)
    store.add(F2, "A", rank=1)
    store.add(F2, "B", rank=2)
    store.endpoint_override[F1] = FOLD_END  # endpoint == fold boundary
    partition, fold, _ = _partition()
    panel = _panel(
        {
            (F2, "A"): 1.0,
            (F2, "B"): 2.0,
        }
    )
    spec = _spec()
    declaration = _declaration(partition, fold, panel, spec)
    case = _run(store, panel=panel, partition=partition, fold=fold, spec=spec,
                declaration=declaration)
    purged = {(cell.formation_date, cell.reason) for cell in case.diagnostics.purged_cells}
    assert (F1, B.PURGED_FOLD_BOUNDARY) in purged
    assert F1 not in case.diagnostics.formations
    assert all(
        record.formation_date != F1 for record in case.diagnostics.accounting_records
    )


def test_item031_factor_panel_date_outside_fold_raises(tmp_path: Path) -> None:
    store = _Store(tmp_path)
    store.standard()
    bindings = store.build()
    partition, fold, _ = _partition()
    spec = _spec()
    values = _standard_values()
    values[(HOLDOUT_START, "A")] = 1.0
    panel = _panel(values)
    declaration = _declaration(partition, fold, panel, spec)
    with pytest.raises(B.FinalHoldoutFirewallError):
        B.build_development_diagnostics(
            store.root, declaration, spec, panel, partition, bindings=bindings
        )


def test_item032_cell_within_fold_is_kept(tmp_path: Path) -> None:
    case = _run(_Store(tmp_path))
    assert set(case.diagnostics.formations) == {F1, F2}
    assert case.diagnostics.purged_cells == ()


def _capture_run(store: _Store, **kwargs: Any):
    from smart_beta.evaluation import engine

    captured: list[pd.DataFrame] = []
    original = engine._metric_observations

    def _capture(panel: pd.DataFrame, portfolio: Any, *args: Any, **kw: Any):
        captured.append(panel.copy())
        return original(panel, portfolio, *args, **kw)

    engine._metric_observations = _capture  # type: ignore[assignment]
    try:
        case = _run(store, **kwargs)
    finally:
        engine._metric_observations = original  # type: ignore[assignment]
    first = captured[0] if captured else None
    second = captured[1] if len(captured) > 1 else None
    return case, first, second


def _forward(panel: pd.DataFrame, stock: str, formation: dt.date) -> float:
    row = panel[
        (panel["stock_id"] == stock)
        & (pd.to_datetime(panel["date"]).dt.date == formation)
    ]
    return float(row["forward_return"].iloc[0])


def _equal_count_different_identity_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **kwargs: Any
) -> None:
    """T0/TS and the expected set have equal pair counts but different identities.

    Only the identity checks (mask positions, pair-set identity) can catch
    this: the per-group n_stocks/n_returns reconciliation, the accounting
    check and the typed-NaN accounting all remain consistent by construction.
    """
    original = B._scenario_panels

    def _patched(**call_kwargs: Any):
        panel_t0, panel_ts, records = original(**call_kwargs)
        candidates = [
            record for record in records
            if record.scenario == B.SCENARIO_T0
            and record.expected_admissible
            and record.formation_date == F1
        ]
        first = candidates[0]
        second = next(
            record for record in candidates
            if record.group == first.group and record.stock_id != first.stock_id
        )

        def row_index(panel: pd.DataFrame, record: Any) -> int:
            mask = (panel["stock_id"] == record.stock_id) & (
                pd.to_datetime(panel["date"]).dt.date == record.formation_date
            )
            return int(panel.index[mask][0])

        # T0: first finite, second NaN.  TS: first NaN, second finite.
        panel_t0.loc[row_index(panel_t0, second), "forward_return"] = np.nan
        panel_ts.loc[row_index(panel_ts, first), "forward_return"] = np.nan

        def adjust(record: Any) -> Any:
            if record.formation_date != first.formation_date:
                return record
            if record.stock_id == first.stock_id:
                return dataclasses.replace(
                    record,
                    expected_admissible=record.scenario == B.SCENARIO_T0,
                )
            if record.stock_id == second.stock_id:
                return dataclasses.replace(
                    record,
                    expected_admissible=record.scenario == B.SCENARIO_TS,
                )
            return record

        return panel_t0, panel_ts, tuple(adjust(record) for record in records)

    monkeypatch.setattr(B, "_scenario_panels", _patched)
    with pytest.raises(B.D7bConsistencyError):
        _capture_run(_Store(tmp_path), **kwargs)


# ===========================================================================
# 33-36: reserve
# ===========================================================================


def test_item033_reserve_crossing_cell_is_purged(tmp_path: Path) -> None:
    store = _Store(tmp_path)
    store.standard()
    store.crosses_reserve[F1] = True
    panel = _panel({(F2, k): v for (f, k), v in _standard_values().items() if f == F2})
    case, panel_t0, _ = _capture_run(store, panel=panel)
    purged = {(cell.formation_date, cell.reason) for cell in case.diagnostics.purged_cells}
    assert (F1, B.PURGED_RESERVED_OUTCOME) in purged


def test_item034_reserve_purged_cell_never_reaches_p7e(tmp_path: Path) -> None:
    store = _Store(tmp_path)
    store.standard()
    store.crosses_reserve[F1] = True
    panel = _panel({(F2, k): v for (f, k), v in _standard_values().items() if f == F2})
    case, panel_t0, panel_ts = _capture_run(store, panel=panel)
    for panel_s in (panel_t0, panel_ts):
        assert set(pd.to_datetime(panel_s["date"]).dt.date) == {F2}


def test_item035_reserve_purged_cell_in_no_denominator(tmp_path: Path) -> None:
    store = _Store(tmp_path)
    store.standard()
    store.crosses_reserve[F1] = True
    panel = _panel({(F2, k): v for (f, k), v in _standard_values().items() if f == F2})
    case, _, _ = _capture_run(store, panel=panel)
    assert case.diagnostics.formations == (F2,)
    assert case.diagnostics.b == 0.0
    assert case.diagnostics.e_s == 0.0


def test_item036_reserve_date_raises_and_non_allowlisted_refused(
    tmp_path: Path,
) -> None:
    store = _Store(tmp_path)
    store.standard()
    store.members[0].observed_segment_end = B.RESERVE_START
    bindings = store.build()
    partition, fold, _ = _partition()
    panel = _panel(_standard_values())
    spec = _spec()
    declaration = _declaration(partition, fold, panel, spec)
    with pytest.raises(B.ReserveFirewallError):
        B.build_development_diagnostics(
            store.root, declaration, spec, panel, partition, bindings=bindings
        )
    resolver = B._Resolver(store.root, bindings)
    with pytest.raises(B.ReserveFirewallError):
        resolver.resolve("reserve_sealed/anything.parquet")
    with pytest.raises(B.ReserveFirewallError):
        resolver.resolve("derived/returns/not-allowlisted/members.parquet")


# ===========================================================================
# 37-50: returns and T0
# ===========================================================================


def test_item037_complete_member_is_p7b_horizon_value(tmp_path: Path) -> None:
    case, panel_t0, panel_ts = _capture_run(_Store(tmp_path))
    for stock, rate in DEFAULT_RATES.items():
        expected = _compound(rate, HORIZON)
        assert _forward(panel_t0, stock, F1) == pytest.approx(expected)
        assert _forward(panel_ts, stock, F1) == pytest.approx(expected)
    assert case.diagnostics.value_t0 == pytest.approx(case.diagnostics.value_ts)


def test_item038_complete_terminal_uses_observed_segment(tmp_path: Path) -> None:
    store = _Store(tmp_path)
    store.standard(terminal="A")
    case, panel_t0, _ = _capture_run(store)
    assert _forward(panel_t0, "A", F1) == pytest.approx(_compound(0.01, 1))
    record = next(
        r for r in case.diagnostics.accounting_records
        if r.scenario == B.SCENARIO_T0 and r.stock_id == "A"
    )
    assert record.admissibility == B.ADMISSIBLE_TERMINAL


def test_item039_listed_end_at_formation_empty_identity(tmp_path: Path) -> None:
    store = _Store(tmp_path)
    store.add(F1, "A", state="EMPTY")
    store.add(F1, "B", rank=2)
    store.add(F2, "A", state="EMPTY")
    store.add(F2, "B", rank=2)
    panel = _panel(_standard_values(keys=("A", "B")))
    case, panel_t0, panel_ts = _capture_run(store, panel=panel)
    assert _forward(panel_t0, "A", F1) == 0.0
    assert _forward(panel_ts, "A", F1) == 1.0  # short +100%
    records = [
        r for r in case.diagnostics.accounting_records
        if r.stock_id == "A"
    ]
    assert all(r.admissibility == B.ADMISSIBLE_TERMINAL_EMPTY for r in records)


def test_item040_terminal_at_endpoint_applies_tau(tmp_path: Path) -> None:
    store = _Store(tmp_path)
    store.add(F1, "A", affected=True, m=HORIZON, rank=1)
    store.add(F1, "B", rank=2)
    store.add(F2, "A", affected=True, m=HORIZON, rank=1)
    store.add(F2, "B", rank=2)
    panel = _panel(_standard_values(keys=("A", "B")))
    case, panel_t0, panel_ts = _capture_run(store, panel=panel)
    r = _compound(0.01, HORIZON)
    assert _forward(panel_t0, "A", F1) == pytest.approx(r)
    assert _forward(panel_ts, "A", F1) == pytest.approx((1 + r) * 2.0 - 1.0)


def test_item041_terminal_after_endpoint_is_not_affected(tmp_path: Path) -> None:
    case, panel_t0, _ = _capture_run(_Store(tmp_path))
    records = [
        r for r in case.diagnostics.accounting_records
        if r.scenario == B.SCENARIO_T0 and r.stock_id == "A"
    ]
    assert records and records[0].admissibility == B.ADMISSIBLE
    assert _forward(panel_t0, "A", F1) == pytest.approx(_compound(0.01, HORIZON))


def test_item042_post_terminal_sessions_never_compounded(tmp_path: Path) -> None:
    store = _Store(tmp_path)
    store.add(
        F1, "A", affected=True, m=1, rank=1,
        daily=[(_future(F1, 1), "VALID", 0.01), (_future(F1, 2), "POST_TERMINAL", 99.0)],
    )
    store.add(F2, "A", affected=True, m=1, rank=1,
              daily=[(_future(F2, 1), "VALID", 0.01), (_future(F2, 2), "POST_TERMINAL", 99.0)])
    panel = _panel({(F1, "A"): 1.0, (F2, "A"): 1.0})
    case, panel_t0, _ = _capture_run(store, panel=panel)
    assert _forward(panel_t0, "A", F1) == pytest.approx(0.01)


def test_item043_exact_t0_and_ts_formulas(tmp_path: Path) -> None:
    store = _Store(tmp_path)
    store.standard(terminal="A")
    _, panel_t0, panel_ts = _capture_run(store)
    r_obs = _compound(0.01, 1)
    assert _forward(panel_t0, "A", F1) == pytest.approx((1 + r_obs) * 1.0 - 1.0)
    assert _forward(panel_ts, "A", F1) == pytest.approx((1 + r_obs) * 2.0 - 1.0)


def test_item044_realization_end_mismatch_raises(tmp_path: Path) -> None:
    store = _Store(tmp_path)
    store.standard(terminal="A")
    store.members[0].observed_segment_end = _future(F1, 2)
    with pytest.raises(B.D7bConsistencyError):
        _capture_run(store)


def test_item045_p7b_status_disagreement_raises(tmp_path: Path) -> None:
    store = _Store(tmp_path)
    store.add(F1, "A", daily=[(_future(F1, 2), "VALID", 0.01)])
    store.add(F1, "B", rank=2)
    store.add(F2, "A", daily=[(_future(F2, 2), "VALID", 0.01)])
    store.add(F2, "B", rank=2)
    panel = _panel(_standard_values(keys=("A", "B")))
    with pytest.raises(B.D7bConsistencyError):
        _capture_run(store, panel=panel)


def test_item046_missing_non_terminal_is_typed_nan(tmp_path: Path) -> None:
    store = _Store(tmp_path)
    store.add(F1, "A", state="INCOMPLETE", rank=1)
    store.add(F1, "B", rank=2)
    store.add(F2, "A", state="INCOMPLETE", rank=1)
    store.add(F2, "B", rank=2)
    panel = _panel(_standard_values(keys=("A", "B")))
    case, panel_t0, panel_ts = _capture_run(store, panel=panel)
    assert math.isnan(_forward(panel_t0, "A", F1))
    record = next(
        r for r in case.diagnostics.accounting_records
        if r.scenario == B.SCENARIO_T0 and r.stock_id == "A"
    )
    assert record.admissibility == B.OBSERVED_PATH_INCOMPLETE
    assert record.expected_admissible is False


def test_item047_terminal_and_incomplete_is_typed_nan(tmp_path: Path) -> None:
    store = _Store(tmp_path)
    store.add(F1, "A", state="INCOMPLETE", affected=True, m=1, rank=1)
    store.add(F1, "B", rank=2)
    store.add(F2, "A", state="INCOMPLETE", affected=True, m=1, rank=1)
    store.add(F2, "B", rank=2)
    panel = _panel(_standard_values(keys=("A", "B")))
    case, panel_t0, panel_ts = _capture_run(store, panel=panel)
    assert math.isnan(_forward(panel_t0, "A", F1))
    assert math.isnan(_forward(panel_ts, "A", F1))
    records = [r for r in case.diagnostics.accounting_records if r.stock_id == "A"]
    assert all(r.admissibility == B.TERMINAL_OBSERVED_PATH_INCOMPLETE for r in records)


def test_item048_tau_does_not_repair_missing_data(tmp_path: Path) -> None:
    store = _Store(tmp_path)
    # C is in the economic long leg (group 2) and terminal-incomplete.
    store.add(F1, "A", rank=1)
    store.add(F1, "B", rank=2)
    store.add(F2, "A", rank=1)
    store.add(F2, "B", rank=2)
    store.add(F1, "C", state="INCOMPLETE", affected=True, m=1, rank=3)
    store.add(F2, "C", state="INCOMPLETE", affected=True, m=1, rank=3)
    panel = _panel(_standard_values(keys=("A", "B", "C")))
    _, panel_t0, panel_ts = _capture_run(store, panel=panel)
    assert math.isnan(_forward(panel_ts, "C", F1))
    assert math.isnan(_forward(panel_ts, "C", F2))


def test_item049_scenario_panels_are_not_persisted(tmp_path: Path) -> None:
    store = _Store(tmp_path)
    store.standard(terminal="A")
    case, panel_t0, panel_ts = _capture_run(store)
    files = sorted(str(p.relative_to(store.root)) for p in store.root.rglob("*"))
    assert all("panel" not in name for name in files)
    payload = case.diagnostics.to_dict()
    assert "panel" not in payload
    assert "forward_return" not in B.canonical_json([r.to_dict() for r in case.diagnostics.accounting_records])


def test_item050_horizons_one_21_and_63_work(tmp_path: Path) -> None:
    for horizon in (1, 21, 63):
        store = _Store(tmp_path, name=f"h{horizon}", horizon=horizon)
        store.standard()
        case, _, _ = _capture_run(store, horizon=horizon)
        assert case.diagnostics.not_assessed is None
        assert case.diagnostics.value_t0 is not None
        assert case.diagnostics.value_t0 == pytest.approx(
            case.diagnostics.value_ts
        )


# ===========================================================================
# 51-53: universes
# ===========================================================================


def test_item051_universes_selected_by_flag_no_reranking(tmp_path: Path) -> None:
    def build(universe: str):
        store = _Store(tmp_path, name=f"u-{universe}")
        for f in (F1, F2):
            store.add(f, "A", rank=99, flag="TOP_200", rate=0.01)
            store.add(f, "B", rank=1, flag="TOP_500", rate=0.02)
            store.add(f, "C", rank=2, top_1000=True, rate=0.03)
        keys = {"TOP_200": ("A",), "TOP_500": ("A", "B"), "TOP_1000": ("A", "B", "C")}[universe]
        panel = _panel(_standard_values(keys=keys))
        return _capture_run(store, panel=panel, universe=universe)

    top_200, _, _ = build("TOP_200")
    top_500, _, _ = build("TOP_500")
    top_1000, _, _ = build("TOP_1000")
    assert top_200.diagnostics.formation_accounting[0].n_universe == 1
    assert top_500.diagnostics.formation_accounting[0].n_universe == 2
    assert top_1000.diagnostics.formation_accounting[0].n_universe == 3


def test_item052_factor_row_for_non_member_raises(tmp_path: Path) -> None:
    store = _Store(tmp_path)
    store.standard()
    bindings = store.build()
    partition, fold, _ = _partition()
    values = _standard_values()
    values[(F1, "Z")] = 5.0
    panel = _panel(values)
    spec = _spec()
    declaration = _declaration(partition, fold, panel, spec)
    with pytest.raises(B.D7bInputError):
        B.build_development_diagnostics(
            store.root, declaration, spec, panel, partition, bindings=bindings
        )


def test_item053_missing_factor_value_has_no_group(tmp_path: Path) -> None:
    store = _Store(tmp_path)
    store.standard()
    values = {k: v for k, v in _standard_values().items() if k[1] != "C"}
    panel = _panel(values)
    case, _, _ = _capture_run(store, panel=panel)
    record = next(
        r for r in case.diagnostics.accounting_records
        if r.scenario == B.SCENARIO_T0 and r.stock_id == "C"
    )
    assert record.group is None
    assert record.admissibility == B.FACTOR_VALUE_MISSING
    formation = next(
        item for item in case.diagnostics.formation_accounting
        if item.formation_date == F1
    )
    assert formation.n_factor_missing == 1
    assert formation.n_short + formation.n_long < formation.n_universe
# ===========================================================================
# 54-79: grouping, NaN and reconciliation
# ===========================================================================


def _inadmissible_store(tmp_path: Path, key: str = "C") -> _Store:
    store = _Store(tmp_path)
    store.standard()
    for member in store.members:
        if member.key == key:
            member.state = "INCOMPLETE"
    return store


def test_item054_inadmissible_member_is_in_p7e_input(tmp_path: Path) -> None:
    store = _inadmissible_store(tmp_path)
    import smart_beta.dataset_v1.delisting_bridge as Mod

    _, panel_t0, panel_ts = _capture_run(store)
    for panel_s in (panel_t0, panel_ts):
        row = panel_s[(panel_s["stock_id"] == "C")]
        assert len(row) == 2
        assert row["forward_return"].isna().all()


def test_item055_inadmissible_member_receives_frozen_group(tmp_path: Path) -> None:
    case, _, _ = _capture_run(_inadmissible_store(tmp_path))
    record = next(
        r for r in case.diagnostics.accounting_records
        if r.scenario == B.SCENARIO_T0 and r.stock_id == "C"
    )
    assert record.group == 2


def test_item056_removing_return_changes_no_group(tmp_path: Path) -> None:
    store_bad = _inadmissible_store(tmp_path, key="C")
    store_good = _Store(tmp_path, name="good")
    store_good.standard()
    case_bad, _, _ = _capture_run(store_bad)
    case_good, _, _ = _capture_run(store_good)
    groups_bad = {
        (r.formation_date, r.stock_id): r.group
        for r in case_bad.diagnostics.accounting_records
        if r.scenario == B.SCENARIO_T0
    }
    groups_good = {
        (r.formation_date, r.stock_id): r.group
        for r in case_good.diagnostics.accounting_records
        if r.scenario == B.SCENARIO_T0
    }
    assert groups_bad == groups_good


def test_item057_expected_n_stocks_includes_inadmissible(tmp_path: Path) -> None:
    case, _, _ = _capture_run(_inadmissible_store(tmp_path))
    formation = case.diagnostics.formation_accounting[0]
    group_two = next(g for g in formation.groups if g.group == 2)
    assert group_two.n_stocks == 2


def test_item058_expected_n_returns_excludes_inadmissible(tmp_path: Path) -> None:
    case, _, _ = _capture_run(_inadmissible_store(tmp_path))
    formation = case.diagnostics.formation_accounting[0]
    group_two = next(g for g in formation.groups if g.group == 2)
    assert group_two.n_returns == 1


def test_item059_n_stocks_matches_replay(tmp_path: Path) -> None:
    case, _, _ = _capture_run(_inadmissible_store(tmp_path))
    checks = dict(case.diagnostics.reconciliation)
    assert checks["sort_replay_T0"] and checks["sort_replay_TS"]


def test_item060_n_returns_matches_replay(tmp_path: Path) -> None:
    case, _, _ = _capture_run(_inadmissible_store(tmp_path))
    checks = dict(case.diagnostics.reconciliation)
    assert checks["accounting_T0"] and checks["accounting_TS"]


def test_item061_injected_n_stocks_mismatch_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = B.portfolio_sort_mod.sort_portfolios

    def _patched(*args: Any, **kwargs: Any) -> pd.DataFrame:
        frame = original(*args, **kwargs).copy()
        frame.loc[frame["group"] == 1, "n_stocks"] += 1
        frame.loc[frame["group"] == 2, "n_stocks"] -= 1
        return frame

    monkeypatch.setattr(B.portfolio_sort_mod, "sort_portfolios", _patched)
    with pytest.raises(B.D7bConsistencyError):
        _capture_run(_inadmissible_store(tmp_path))


def test_item062_injected_n_returns_mismatch_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = B.portfolio_sort_mod.sort_portfolios

    def _patched(*args: Any, **kwargs: Any) -> pd.DataFrame:
        frame = original(*args, **kwargs).copy()
        frame["n_returns"] = frame["n_returns"] + 1
        return frame

    monkeypatch.setattr(B.portfolio_sort_mod, "sort_portfolios", _patched)
    with pytest.raises(B.D7bConsistencyError):
        _capture_run(_inadmissible_store(tmp_path))


def test_item063_unaccounted_nan_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = B._scenario_panels

    def _patched(**kwargs: Any):
        panel_t0, panel_ts, records = original(**kwargs)
        victim = next(
            r for r in records
            if r.scenario == B.SCENARIO_T0 and r.expected_admissible
        )
        records = tuple(
            r for r in records
            if not (
                r.formation_date == victim.formation_date
                and r.stock_id == victim.stock_id
            )
        )
        mask = (panel_t0["stock_id"] == victim.stock_id) & (
            pd.to_datetime(panel_t0["date"]).dt.date == victim.formation_date
        )
        panel_t0.loc[panel_t0.index[mask][0], "forward_return"] = np.nan
        panel_ts.loc[panel_ts.index[mask][0], "forward_return"] = np.nan
        return panel_t0, panel_ts, records

    monkeypatch.setattr(B, "_scenario_panels", _patched)
    with pytest.raises(B.D7bConsistencyError):
        _capture_run(_inadmissible_store(tmp_path))


def test_item064_accounted_typed_nan_is_permitted(tmp_path: Path) -> None:
    case, _, _ = _capture_run(_inadmissible_store(tmp_path))
    assert case.diagnostics.not_assessed is None
    record = next(
        r for r in case.diagnostics.accounting_records
        if r.scenario == B.SCENARIO_T0 and r.stock_id == "C"
    )
    assert record.expected_admissible is False


def test_item065_nan_masks_identical_across_scenarios(tmp_path: Path) -> None:
    _, panel_t0, panel_ts = _capture_run(_inadmissible_store(tmp_path))
    assert panel_t0["forward_return"].isna().tolist() == (
        panel_ts["forward_return"].isna().tolist()
    )


def test_item066_admissible_identities_identical_across_scenarios(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, panel_t0, panel_ts = _capture_run(_inadmissible_store(tmp_path))
    assert B._finite_pairs(panel_t0) == B._finite_pairs(panel_ts)
    _equal_count_different_identity_fails(tmp_path, monkeypatch)


def test_item067_group_assignments_identical_across_scenarios(tmp_path: Path) -> None:
    store = _inadmissible_store(tmp_path)
    _, panel_t0, panel_ts = _capture_run(store)
    assert B._frozen_groups(panel_t0, 2) == B._frozen_groups(panel_ts, 2)


def test_item068_terminal_incomplete_stays_nan_under_ts_long(tmp_path: Path) -> None:
    store = _Store(tmp_path)
    store.add(F1, "A", rank=1)
    store.add(F1, "B", rank=2)
    store.add(F2, "A", rank=1)
    store.add(F2, "B", rank=2)
    store.add(F1, "C", state="INCOMPLETE", affected=True, m=1, rank=3)
    store.add(F2, "C", state="INCOMPLETE", affected=True, m=1, rank=3)
    panel = _panel(_standard_values(keys=("A", "B", "C")))
    _, _, panel_ts = _capture_run(store, panel=panel)
    assert math.isnan(_forward(panel_ts, "C", F1))


def test_item069_es_denominator_includes_inadmissible_short(tmp_path: Path) -> None:
    store = _Store(tmp_path)
    store.standard()
    for member in store.members:
        if member.key == "A":
            member.state = "INCOMPLETE"
    case, _, _ = _capture_run(store)
    formation = case.diagnostics.formation_accounting[0]
    assert formation.n_short == 2
    assert formation.n_affected_short == 0
    assert case.diagnostics.e_s == 0.0


def test_item070_el_denominator_includes_inadmissible_long(tmp_path: Path) -> None:
    store = _inadmissible_store(tmp_path, key="D")
    case, _, _ = _capture_run(store)
    formation = case.diagnostics.formation_accounting[0]
    assert formation.n_long == 2
    assert case.diagnostics.e_l == 0.0


def test_item071_b_denominator_includes_inadmissible(tmp_path: Path) -> None:
    store = _inadmissible_store(tmp_path)
    case, _, _ = _capture_run(store)
    assert case.diagnostics.formation_accounting[0].n_universe == 4
    assert case.diagnostics.b == 0.0


def test_item072_ic_expected_pairs_exclude_inadmissible(tmp_path: Path) -> None:
    case, _, _ = _capture_run(
        _inadmissible_store(tmp_path), primary_metric=B.spec_mod.MetricKey.IC, basis="NONE"
    )
    expected = {
        (r.formation_date, r.stock_id)
        for r in case.diagnostics.accounting_records
        if r.scenario == B.SCENARIO_T0 and r.group is not None and r.expected_admissible
    }
    assert all(stock != "C" for _, stock in expected)


def test_item073_rank_ic_expected_pairs_exclude_inadmissible(
    tmp_path: Path,
) -> None:
    case, _, _ = _capture_run(
        _inadmissible_store(tmp_path),
        primary_metric=B.spec_mod.MetricKey.RANK_IC,
        basis="NONE",
    )
    assert case.diagnostics.not_assessed is None
    expected = {
        (r.formation_date, r.stock_id)
        for r in case.diagnostics.accounting_records
        if r.scenario == B.SCENARIO_T0 and r.group is not None and r.expected_admissible
    }
    assert all(stock != "C" for _, stock in expected)


def test_item074_ic_pair_identities_identical_across_scenarios(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, panel_t0, panel_ts = _capture_run(
        _inadmissible_store(tmp_path), primary_metric=B.spec_mod.MetricKey.IC, basis="NONE"
    )
    assert B._finite_pairs(panel_t0) == B._finite_pairs(panel_ts)
    _equal_count_different_identity_fails(
        tmp_path,
        monkeypatch,
        primary_metric=B.spec_mod.MetricKey.IC,
        basis="NONE",
    )


def test_item075_admissibility_cannot_trigger_regrouping(tmp_path: Path) -> None:
    store_bad = _inadmissible_store(tmp_path, key="C")
    case_bad, _, _ = _capture_run(store_bad)
    group_bad = next(
        r.group for r in case_bad.diagnostics.accounting_records
        if r.scenario == B.SCENARIO_T0 and r.stock_id == "C"
    )
    store_good = _Store(tmp_path, name="good")
    store_good.standard()
    case_good, _, _ = _capture_run(store_good)
    group_good = next(
        r.group for r in case_good.diagnostics.accounting_records
        if r.scenario == B.SCENARIO_T0 and r.stock_id == "C"
    )
    assert group_bad == group_good


def test_item076_separate_grouping_rejected_by_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = B._frozen_groups
    calls = {"n": 0}

    def _patched(panel: pd.DataFrame, n_groups: int) -> dict:
        calls["n"] += 1
        groups = original(panel, n_groups)
        if calls["n"] > 1:
            for key, value in groups.items():
                if not math.isnan(value):
                    groups[key] = value + 1
                    break
        return groups

    monkeypatch.setattr(B, "_frozen_groups", _patched)
    with pytest.raises(B.D7bConsistencyError):
        _capture_run(_inadmissible_store(tmp_path))


def test_item077_replayed_spread_equals_p7e_exactly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = B.portfolio_sort_mod.long_short_return
    calls = {"n": 0}

    def _patched(*args: Any, **kwargs: Any) -> pd.Series:
        calls["n"] += 1
        series = original(*args, **kwargs)
        if calls["n"] > 2:
            series = series + 0.5
        return series

    monkeypatch.setattr(B.portfolio_sort_mod, "long_short_return", _patched)
    with pytest.raises(B.D7bConsistencyError):
        _capture_run(_inadmissible_store(tmp_path))


def test_item078_n_members_equals_sum_n_stocks(tmp_path: Path) -> None:
    from smart_beta.evaluation import engine

    captured: list[Any] = []
    original = engine._metric_observations

    def _capture(panel: pd.DataFrame, portfolio: Any, *args: Any, **kw: Any):
        captured.append(portfolio)
        return original(panel, portfolio, *args, **kw)

    engine._metric_observations = _capture  # type: ignore[assignment]
    try:
        case, _, _ = _capture_run(_inadmissible_store(tmp_path))
    finally:
        engine._metric_observations = original  # type: ignore[assignment]
    for portfolio in captured:
        accounting = portfolio.accounting
        for row in accounting.itertuples(index=False):
            groups_at_date = [
                r for r in case.diagnostics.accounting_records
                if r.scenario == B.SCENARIO_T0
                and r.formation_date == pd.Timestamp(row.date).date()
                and r.group is not None
            ]
            assert int(row.n_members) == len(groups_at_date)


def test_item079_shuffled_panel_with_ties_is_identical(tmp_path: Path) -> None:
    values = {
        (F1, "A"): 1.0, (F1, "B"): 1.0, (F1, "C"): 2.0, (F1, "D"): 2.0,
        (F2, "A"): 1.0, (F2, "B"): 1.0, (F2, "C"): 2.0, (F2, "D"): 2.0,
    }
    panel = _panel(values)
    shuffled = panel.iloc[::-1].reset_index(drop=True)
    store_a = _Store(tmp_path, name="a")
    store_a.standard()
    store_b = _Store(tmp_path, name="b")
    store_b.standard()
    case_a, _, _ = _capture_run(store_a, panel=panel)
    case_b, _, _ = _capture_run(store_b, panel=shuffled)
    assert case_a.diagnostics.diagnostics_hash == case_b.diagnostics.diagnostics_hash
# ===========================================================================
# 80-105: sign and orientation
# ===========================================================================


def _varying_store(tmp_path: Path, name: str = "varying", keys=("A", "B", "C", "D")) -> _Store:
    store = _Store(tmp_path, name=name)
    for f, base in ((F1, 0.0), (F2, 0.05)):
        for i, key in enumerate(keys):
            store.add(f, key, rank=i + 1, rate=base + 0.01 * (i + 1))
    return store


def _orientation(tmp_path: Path, metric: Any, basis: str, **kwargs: Any):
    store = _varying_store(tmp_path)
    plus, _, _ = _capture_run(
        store, sign=1, primary_metric=metric, basis=basis, **kwargs
    )
    minus, _, _ = _capture_run(
        store, sign=-1, primary_metric=metric, basis=basis, **kwargs
    )
    return plus, minus


def test_item080_sign_plus_one_ic_unchanged(tmp_path: Path) -> None:
    case, _, _ = _capture_run(
        _varying_store(tmp_path),
        sign=1,
        primary_metric=B.spec_mod.MetricKey.IC,
        basis="NONE",
    )
    assert case.diagnostics.not_assessed is None
    assert case.diagnostics.economic_t0 == case.diagnostics.value_t0


def test_item081_sign_minus_one_ic_negated(tmp_path: Path) -> None:
    plus, minus = _orientation(tmp_path, B.spec_mod.MetricKey.IC, "NONE")
    assert plus.diagnostics.value_t0 is not None
    assert minus.diagnostics.economic_t0 == -plus.diagnostics.value_t0


def test_item082_sign_minus_one_rank_ic_negated(tmp_path: Path) -> None:
    plus, minus = _orientation(tmp_path, B.spec_mod.MetricKey.RANK_IC, "NONE")
    assert plus.diagnostics.value_t0 is not None
    assert minus.diagnostics.economic_t0 == -plus.diagnostics.value_t0


def test_item083_sign_minus_one_long_short_mean_negated(tmp_path: Path) -> None:
    plus, minus = _orientation(tmp_path, B.spec_mod.MetricKey.LONG_SHORT, "GROSS")
    assert plus.diagnostics.value_t0 is not None
    assert minus.diagnostics.economic_t0 == -plus.diagnostics.value_t0


def test_item084_sign_minus_one_long_short_t_negated(tmp_path: Path) -> None:
    plus, minus = _orientation(tmp_path, B.spec_mod.MetricKey.LONG_SHORT, "GROSS")
    assert plus.diagnostics.t_t0 is not None
    assert minus.diagnostics.t_t0 == -plus.diagnostics.t_t0


def test_item085_sign_minus_one_sharpe_negated(tmp_path: Path) -> None:
    plus, minus = _orientation(tmp_path, B.spec_mod.MetricKey.SHARPE, "GROSS")
    assert plus.diagnostics.value_t0 is not None
    assert minus.diagnostics.economic_t0 == -plus.diagnostics.value_t0


def test_item086_sign_minus_one_net_is_unsupported(tmp_path: Path) -> None:
    case, _, _ = _capture_run(
        _varying_store(tmp_path),
        sign=-1,
        primary_metric=B.spec_mod.MetricKey.TURNOVER_COST_ADJUSTED,
        basis="NET",
    )
    assert case.diagnostics.not_assessed == B.NOT_ASSESSED_UNSUPPORTED_ORIENTATION


def test_item087_recorded_net_never_negated(tmp_path: Path) -> None:
    case, _, _ = _capture_run(
        _varying_store(tmp_path),
        sign=-1,
        primary_metric=B.spec_mod.MetricKey.TURNOVER_COST_ADJUSTED,
        basis="NET",
    )
    assert case.diagnostics.economic_t0 is None
    assert case.diagnostics.not_assessed == B.NOT_ASSESSED_UNSUPPORTED_ORIENTATION


def test_item088_sign_minus_one_max_drawdown_unsupported(tmp_path: Path) -> None:
    case, _, _ = _capture_run(
        _varying_store(tmp_path),
        sign=-1,
        primary_metric=B.spec_mod.MetricKey.MAX_DRAWDOWN,
        basis="GROSS",
    )
    assert case.diagnostics.not_assessed == B.NOT_ASSESSED_UNSUPPORTED_ORIENTATION


def test_item089_sign_minus_one_benchmark_relative_not_assessed(tmp_path: Path) -> None:
    case, _, _ = _capture_run(
        _varying_store(tmp_path),
        sign=-1,
        primary_metric=B.spec_mod.MetricKey.BENCHMARK_RELATIVE,
        basis="NONE",
    )
    assert case.diagnostics.not_assessed.startswith("NOT_ASSESSED")


def test_item090_sign_minus_one_unknown_key_not_assessed(tmp_path: Path) -> None:
    case, _, _ = _capture_run(
        _varying_store(tmp_path),
        sign=-1,
        primary_metric=B.spec_mod.MetricKey.SUBPERIOD,
        basis="NONE",
    )
    assert case.diagnostics.not_assessed == B.STATUS_NOT_ASSESSED


def test_item091_sign_minus_one_swaps_economic_legs(tmp_path: Path) -> None:
    plus, minus = _orientation(tmp_path, B.spec_mod.MetricKey.LONG_SHORT, "GROSS")
    plus_legs = {
        (r.formation_date, r.stock_id): (r.group, r.economic_leg)
        for r in plus.diagnostics.accounting_records
        if r.scenario == B.SCENARIO_T0
    }
    minus_legs = {
        (r.formation_date, r.stock_id): (r.group, r.economic_leg)
        for r in minus.diagnostics.accounting_records
        if r.scenario == B.SCENARIO_T0
    }
    assert {k: v[0] for k, v in plus_legs.items()} == {
        k: v[0] for k, v in minus_legs.items()
    }
    for key in plus_legs:
        assert plus_legs[key][1] != minus_legs[key][1]


def test_item092_sign_minus_one_ts_tau(tmp_path: Path) -> None:
    store = _Store(tmp_path)
    store.standard(terminal="A")
    panel = _panel(_standard_values())
    _, panel_t0, panel_ts = _capture_run(store, panel=panel, sign=-1,
                                         primary_metric=B.spec_mod.MetricKey.LONG_SHORT,
                                         basis="GROSS")
    r = _compound(0.01, 1)
    # A is raw bottom (group 1); under sign=-1 it is the economic long.
    assert _forward(panel_t0, "A", F1) == pytest.approx(r)
    assert _forward(panel_ts, "A", F1) == pytest.approx(-1.0)

    # Under sign=-1 a terminal member in the MIDDLE group gets tau = 0.
    keys = ("A", "B", "C", "D", "E", "F")
    middle_store = _Store(tmp_path, name="middle-store")
    for f in (F1, F2):
        for i, key in enumerate(keys):
            kw: dict[str, Any] = {"rank": i + 1, "rate": 0.01 * (i + 1)}
            if key == "C":
                kw.update(affected=True, m=1)
            middle_store.add(f, key, **kw)
    middle_values = {
        (f, key): float(i + 1) for f in (F1, F2) for i, key in enumerate(keys)
    }
    middle_panel = _panel(middle_values)
    _, middle_t0, middle_ts = _capture_run(
        middle_store,
        panel=middle_panel,
        sign=-1,
        n_groups=3,
        primary_metric=B.spec_mod.MetricKey.LONG_SHORT,
        basis="GROSS",
    )
    r_c = _compound(0.03, 1)
    assert _forward(middle_t0, "C", F1) == pytest.approx(r_c)
    assert _forward(middle_ts, "C", F1) == pytest.approx(r_c)


def test_item093_sign_plus_one_ts_tau_raw_extremes(tmp_path: Path) -> None:
    store = _Store(tmp_path, horizon=HORIZON)
    keys = ("A", "B", "C", "D", "E", "F")
    for f in (F1, F2):
        for i, key in enumerate(keys):
            kw: dict[str, Any] = {"rank": i + 1, "rate": 0.01 * (i + 1)}
            if key in ("A", "C", "F"):
                kw.update(affected=True, m=1)
            store.add(f, key, **kw)
    values = {(f, k): float(i + 1) for f in (F1, F2) for i, k in enumerate(keys)}
    panel = _panel(values)
    _, panel_t0, panel_ts = _capture_run(store, panel=panel, n_groups=3)
    r_a = _compound(0.01, 1)
    r_c = _compound(0.03, 1)
    r_f = _compound(0.06, 1)
    assert _forward(panel_ts, "A", F1) == pytest.approx((1 + r_a) * 2.0 - 1.0)
    assert _forward(panel_ts, "C", F1) == pytest.approx(r_c)
    assert _forward(panel_ts, "F", F1) == pytest.approx((1 + r_f) * 0.0 - 1.0)


def test_item094_sign_minus_one_swaps_es_el_extremes(tmp_path: Path) -> None:
    store = _varying_store(tmp_path, keys=("A", "B", "C"))
    panel = _panel(_standard_values(keys=("A", "B", "C")))
    plus, _, _ = _capture_run(
        store, panel=panel, sign=1,
        primary_metric=B.spec_mod.MetricKey.LONG_SHORT, basis="GROSS",
    )
    minus, _, _ = _capture_run(
        store, panel=panel, sign=-1,
        primary_metric=B.spec_mod.MetricKey.LONG_SHORT, basis="GROSS",
    )
    plus_form = plus.diagnostics.formation_accounting[0]
    minus_form = minus.diagnostics.formation_accounting[0]
    assert plus_form.n_short == minus_form.n_long
    assert plus_form.n_long == minus_form.n_short


def test_item095_orientation_rule_identical_for_t0_and_ts(tmp_path: Path) -> None:
    _, minus = _orientation(tmp_path, B.spec_mod.MetricKey.IC, "NONE")
    assert minus.diagnostics.economic_t0 == -minus.diagnostics.value_t0
    assert minus.diagnostics.economic_ts == -minus.diagnostics.value_ts


def test_item096_clause_a_compares_economic_t0_and_ts(tmp_path: Path) -> None:
    store = _Store(tmp_path)
    store.standard(terminal="A")
    case, _, _ = _capture_run(store)
    record = case.classify(t0=False, ts=False)
    assert record.clause_a is True
    assert record.status == B.STATUS_DELISTING_SENSITIVE


def test_item097_factor_spec_identity_unchanged(tmp_path: Path) -> None:
    store = _varying_store(tmp_path)
    spec = _spec(sign=-1)
    case, _, _ = _capture_run(store, sign=-1, spec=spec,
                              primary_metric=B.spec_mod.MetricKey.IC, basis="NONE")
    assert spec.version == case.declaration.factor_spec_version
    assert case.diagnostics.factor_spec_version == spec.version


def test_item098_input_factor_values_unchanged(tmp_path: Path) -> None:
    store = _varying_store(tmp_path)
    panel = _panel(_standard_values())
    original = panel.copy(deep=True)
    _capture_run(store, panel=panel)
    pd.testing.assert_frame_equal(panel, original)


def test_item099_no_reranking(tmp_path: Path) -> None:
    store = _Store(tmp_path)
    for f in (F1, F2):
        store.add(f, "A", rank=99, rate=0.01)
        store.add(f, "B", rank=1, rate=0.02)
    panel = _panel(_standard_values(keys=("A", "B")))
    case, _, _ = _capture_run(store, panel=panel)
    formation = case.diagnostics.formation_accounting[0]
    assert formation.n_universe == 2
    group_a = next(
        r.group for r in case.diagnostics.accounting_records
        if r.scenario == B.SCENARIO_T0 and r.stock_id == "A"
    )
    assert group_a == 1


def test_item100_no_second_net_series_constructed() -> None:
    source = Path(B.__file__).read_text()
    assert "net_returns =" not in source
    assert "net_series" not in source


def test_item101_unsupported_orientation_cannot_advance(tmp_path: Path) -> None:
    case, _, _ = _capture_run(
        _varying_store(tmp_path),
        sign=-1,
        primary_metric=B.spec_mod.MetricKey.TURNOVER_COST_ADJUSTED,
        basis="NET",
    )
    record = case.classify()
    assert record.status == B.STATUS_NOT_ASSESSED
    assert record.advancement_blocked is True


def test_item102_sign_plus_one_net_matches_glue(tmp_path: Path) -> None:
    from smart_beta.evaluation import engine
    from smart_beta.evaluation.metrics import long_short_mean_tstat

    captured: list[Any] = []
    original = engine._metric_observations

    def _capture(panel: pd.DataFrame, portfolio: Any, *args: Any, **kw: Any):
        captured.append(portfolio)
        return original(panel, portfolio, *args, **kw)

    engine._metric_observations = _capture  # type: ignore[assignment]
    try:
        case, _, _ = _capture_run(
            _varying_store(tmp_path),
            sign=1,
            primary_metric=B.spec_mod.MetricKey.TURNOVER_COST_ADJUSTED,
            basis="NET",
        )
    finally:
        engine._metric_observations = original  # type: ignore[assignment]
    assert captured[0].cost_bps == case.declaration.transaction_cost_bps
    expected = long_short_mean_tstat(captured[0].net_returns)
    assert case.diagnostics.value_t0 == expected.to_metric_value(
        B.spec_mod.MetricKey.TURNOVER_COST_ADJUSTED
    ).value


def test_item103_sign_plus_one_benchmark_no_authority(tmp_path: Path) -> None:
    case, _, _ = _capture_run(
        _varying_store(tmp_path),
        sign=1,
        primary_metric=B.spec_mod.MetricKey.BENCHMARK_RELATIVE,
        basis="NONE",
    )
    assert case.diagnostics.not_assessed == B.NOT_ASSESSED_NO_BENCHMARK_AUTHORITY


def test_item104_basis_key_inconsistency(tmp_path: Path) -> None:
    case, _, _ = _capture_run(
        _varying_store(tmp_path),
        sign=1,
        primary_metric=B.spec_mod.MetricKey.IC,
        basis="NET",
    )
    assert case.diagnostics.not_assessed == B.NOT_ASSESSED_DECLARATION_INCONSISTENT


def test_item105_value_weight_unsupported(tmp_path: Path) -> None:
    case, _, _ = _capture_run(
        _varying_store(tmp_path), weighting="VALUE"
    )
    assert case.diagnostics.not_assessed == B.NOT_ASSESSED_VALUE_WEIGHT_UNSUPPORTED
# ===========================================================================
# 106-126: B, E and section 9.6
# ===========================================================================


def _terminal_a(tmp_path: Path, name: str = "store") -> _Store:
    store = _Store(tmp_path, name=name)
    store.standard(terminal="A")
    return store


def test_item106_b_hand_computed(tmp_path: Path) -> None:
    case, _, _ = _capture_run(_terminal_a(tmp_path))
    # One of four members is terminal-affected in each formation.
    assert case.diagnostics.b == pytest.approx(0.25)
    assert case.diagnostics.n_b_formations == 2


def test_item107_es_hand_computed(tmp_path: Path) -> None:
    case, _, _ = _capture_run(_terminal_a(tmp_path))
    # Short leg is {A, B}; one affected (A).
    assert case.diagnostics.e_s == pytest.approx(0.5)
    assert case.diagnostics.n_es_formations == 2


def test_item108_el_hand_computed_and_not_used(tmp_path: Path) -> None:
    store = _Store(tmp_path)
    store.standard(terminal="D")
    case, _, _ = _capture_run(store)
    assert case.diagnostics.e_l == pytest.approx(0.5)
    override = _classify_override(
        case, e_s=0.0, e_l=10.0, economic_t0=1.0, economic_ts=1.0
    )
    assert override.status == B.STATUS_NOT_SENSITIVE


def test_item109_b_denominator_independent_of_returns(tmp_path: Path) -> None:
    bad = _Store(tmp_path, name="bad")
    bad.standard(terminal="A")
    for member in bad.members:
        if member.key == "C":
            member.state = "INCOMPLETE"
    good = _Store(tmp_path, name="good")
    good.standard(terminal="A")
    case_bad, _, _ = _capture_run(bad)
    case_good, _, _ = _capture_run(good)
    assert case_bad.diagnostics.b == case_good.diagnostics.b == 0.25


def test_item110_terminal_incomplete_counts_in_b(tmp_path: Path) -> None:
    store = _Store(tmp_path)
    store.standard()
    for member in store.members:
        if member.key == "A":
            member.state = "INCOMPLETE"
            member.affected = True
            member.m = 1
    case, _, _ = _capture_run(store)
    assert case.diagnostics.formation_accounting[0].n_affected == 1
    assert case.diagnostics.b == pytest.approx(0.25)


def test_item111_terminal_incomplete_counts_by_leg(tmp_path: Path) -> None:
    store = _Store(tmp_path)
    store.standard()
    for member in store.members:
        if member.key == "A":
            member.state = "INCOMPLETE"
            member.affected = True
            member.m = 1
    case, _, _ = _capture_run(store)
    formation = case.diagnostics.formation_accounting[0]
    assert formation.n_affected_short == 1
    assert formation.n_short == 2


def _classify_override(case: _Case, **overrides: Any):
    diag = dataclasses.replace(case.diagnostics, **overrides)
    t0 = B.ScreenOutcome(
        diag.screen_id, diag.screen_policy_hash, diag.declaration_hash,
        diag.diagnostics_hash, B.SCENARIO_T0, True,
    )
    ts = B.ScreenOutcome(
        diag.screen_id, diag.screen_policy_hash, diag.declaration_hash,
        diag.diagnostics_hash, B.SCENARIO_TS, True,
    )
    return B.classify_delisting_sensitivity(diag, t0, ts)


def test_item112_es_equals_two_b_not_sensitive(tmp_path: Path) -> None:
    case = _run(_Store(tmp_path))
    record = _classify_override(
        case, e_s=1.0, b=0.5, economic_t0=1.0, economic_ts=1.0
    )
    assert record.clause_c is False
    assert record.status == B.STATUS_NOT_SENSITIVE


def test_item113_es_just_above_two_b_sensitive(tmp_path: Path) -> None:
    case = _run(_Store(tmp_path))
    just_above = math.nextafter(2.0 * 0.5, math.inf)
    assert just_above > 2.0 * 0.5
    record = _classify_override(
        case, e_s=just_above, b=0.5, economic_t0=1.0, economic_ts=1.0
    )
    assert record.clause_c is True
    assert record.status == B.STATUS_DELISTING_SENSITIVE


def test_item114_b_zero_es_zero_not_sensitive(tmp_path: Path) -> None:
    case = _run(_Store(tmp_path))
    record = _classify_override(
        case, e_s=0.0, b=0.0, economic_t0=1.0, economic_ts=1.0
    )
    assert record.clause_c is False
    assert record.status == B.STATUS_NOT_SENSITIVE


def test_item115_b_zero_es_positive_sensitive(tmp_path: Path) -> None:
    case = _run(_Store(tmp_path))
    record = _classify_override(
        case, e_s=0.5, b=0.0, economic_t0=1.0, economic_ts=1.0
    )
    assert record.clause_c is True
    assert record.status == B.STATUS_DELISTING_SENSITIVE


def test_item116_positive_t0_negative_ts_is_sensitive(tmp_path: Path) -> None:
    store = _Store(tmp_path)
    store.standard(terminal="A")
    case, _, _ = _capture_run(store)
    assert case.diagnostics.economic_t0 > 0 > case.diagnostics.economic_ts
    record = case.classify(t0=True, ts=True)
    assert record.clause_a is True
    assert record.status == B.STATUS_DELISTING_SENSITIVE


def test_item117_sign_zero_cases(tmp_path: Path) -> None:
    case = _run(_Store(tmp_path))
    zero_to_positive = _classify_override(
        case, e_s=0.0, b=0.0, economic_t0=0.0, economic_ts=1.0
    )
    assert zero_to_positive.clause_a is True
    both_zero = _classify_override(
        case, e_s=0.0, b=0.0, economic_t0=0.0, economic_ts=0.0
    )
    assert both_zero.clause_a is False
    assert both_zero.status == B.STATUS_NOT_SENSITIVE


def test_item118_nan_primary_statistic_is_not_assessed(tmp_path: Path) -> None:
    case = _run(_Store(tmp_path))
    record = _classify_override(
        case, e_s=0.0, b=0.0, economic_t0=None, economic_ts=1.0
    )
    assert record.status == B.STATUS_NOT_ASSESSED
    assert record.advancement_blocked is True


def test_item119_screen_verdict_clause(tmp_path: Path) -> None:
    case = _run(_Store(tmp_path))
    sensitive = case.classify(t0=True, ts=False)
    assert sensitive.clause_b is True
    assert sensitive.status == B.STATUS_DELISTING_SENSITIVE
    not_sensitive = case.classify(t0=False, ts=True)
    assert not_sensitive.clause_b is False
    assert not_sensitive.status == B.STATUS_NOT_SENSITIVE


def test_item120_screen_mismatch_is_not_assessed(tmp_path: Path) -> None:
    case = _run(_Store(tmp_path))
    good_t0, good_ts = case.screens()
    broken = dataclasses.replace(good_ts, screen_id="someone-else")
    record = B.classify_delisting_sensitivity(
        case.diagnostics, good_t0, broken
    )
    assert record.status == B.STATUS_NOT_ASSESSED
    assert record.not_assessed_reason == B.NOT_ASSESSED_SCREEN_MISMATCH


def test_item121_missing_declaration_or_screen(tmp_path: Path) -> None:
    store = _Store(tmp_path)
    store.standard()
    bindings = store.build()
    partition, fold, _ = _partition()
    panel = _panel(_standard_values())
    spec = _spec()
    diag = B.build_development_diagnostics(
        store.root, None, spec, panel, partition, bindings=bindings
    )
    record = B.classify_delisting_sensitivity(diag, None, None)
    assert record.status == B.STATUS_NOT_ASSESSED
    assert record.advancement_blocked is True
    case = _run(store, panel=panel)
    missing = B.classify_delisting_sensitivity(case.diagnostics, None, None)
    assert missing.not_assessed_reason == B.NOT_ASSESSED_SCREEN_MISSING


def test_item122_only_declared_metric_is_computed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _raise(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("an undeclared metric was computed")

    monkeypatch.setattr(B.engine_mod, "information_coefficient", _raise)
    monkeypatch.setattr(B.engine_mod, "rank_information_coefficient", _raise)
    monkeypatch.setattr(B.engine_mod, "sharpe_ratio", _raise)
    monkeypatch.setattr(B.engine_mod, "max_drawdown", _raise)
    case, _, _ = _capture_run(_varying_store(tmp_path))
    assert case.diagnostics.not_assessed is None


def test_item123_changed_declaration_refused(tmp_path: Path) -> None:
    case = _run(_Store(tmp_path))
    changed = dataclasses.replace(
        case.declaration, precommitment_ref="a-different-precommitment"
    )
    assert changed.declaration_hash != case.declaration.declaration_hash
    broken_t0 = B.ScreenOutcome(
        case.diagnostics.screen_id, case.diagnostics.screen_policy_hash,
        changed.declaration_hash, case.diagnostics.diagnostics_hash,
        B.SCENARIO_T0, True,
    )
    _, good_ts = case.screens()
    record = B.classify_delisting_sensitivity(
        case.diagnostics, broken_t0, good_ts
    )
    assert record.status == B.STATUS_NOT_ASSESSED
    assert record.not_assessed_reason == B.NOT_ASSESSED_SCREEN_MISMATCH


def test_item124_per_formation_group_counts_and_fractions(tmp_path: Path) -> None:
    case, _, _ = _capture_run(_terminal_a(tmp_path))
    formation = case.diagnostics.formation_accounting[0]
    assert formation.affected_short_fraction is not None
    assert formation.affected_long_fraction is not None
    assert all(group.n_affected >= 0 for group in formation.groups)
    assert any(group.n_affected == 1 for group in formation.groups)


def test_item125_affected_fraction_denominators_identical(tmp_path: Path) -> None:
    case, _, _ = _capture_run(_terminal_a(tmp_path))
    by_scenario: dict[str, dict[dt.date, list[int]]] = {}
    for record in case.diagnostics.accounting_records:
        bucket = by_scenario.setdefault(record.scenario, {}).setdefault(
            record.formation_date, [0, 0]
        )
        if record.economic_leg == B.ECONOMIC_SHORT:
            bucket[0] += 1
        elif record.economic_leg == B.ECONOMIC_LONG:
            bucket[1] += 1
    assert by_scenario[B.SCENARIO_T0] == by_scenario[B.SCENARIO_TS]


def test_item126_turnover_and_costs_identical(tmp_path: Path) -> None:
    from smart_beta.evaluation import engine

    captured: list[Any] = []
    original = engine._metric_observations

    def _capture(panel: pd.DataFrame, portfolio: Any, *args: Any, **kw: Any):
        captured.append(portfolio)
        return original(panel, portfolio, *args, **kw)

    store = _Store(tmp_path)
    store.standard(terminal="A")
    panel = _panel(
        {
            (F1, "A"): 1.0, (F1, "B"): 2.0, (F1, "C"): 3.0, (F1, "D"): 4.0,
            (F2, "A"): 4.0, (F2, "B"): 3.0, (F2, "C"): 2.0, (F2, "D"): 1.0,
        }
    )
    engine._metric_observations = _capture  # type: ignore[assignment]
    try:
        case, _, _ = _capture_run(
            store, panel=panel, transaction_cost_bps=10.0
        )
    finally:
        engine._metric_observations = original  # type: ignore[assignment]
    t0, ts = captured[0], captured[1]
    assert sum(t0.turnover.tolist()) > 0.0
    assert t0.turnover.tolist() == ts.turnover.tolist()
    assert t0.costs.tolist() == ts.costs.tolist()


# ===========================================================================
# 127-133: determinism and hygiene
# ===========================================================================


def test_item127_two_runs_identical_hashes(tmp_path: Path) -> None:
    store = _Store(tmp_path)
    store.standard(terminal="A")
    first, _, _ = _capture_run(store)
    second, _, _ = _capture_run(store)
    assert first.diagnostics.diagnostics_hash == second.diagnostics.diagnostics_hash
    assert first.classify().record_hash == second.classify().record_hash


def test_item128_timezone_and_locale_do_not_matter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _Store(tmp_path)
    store.standard(terminal="A")
    first, _, _ = _capture_run(store)
    monkeypatch.setenv("TZ", "Pacific/Kiritimati")
    monkeypatch.setenv("LC_ALL", "C.UTF-8")
    second, _, _ = _capture_run(store)
    assert first.diagnostics.diagnostics_hash == second.diagnostics.diagnostics_hash


def test_item129_permuted_inputs_identical_output(tmp_path: Path) -> None:
    store = _Store(tmp_path, name="a")
    store.standard(terminal="A")
    values = _standard_values()
    panel = _panel(values)
    case_a, _, _ = _capture_run(store, panel=panel)
    # (a) permuting only the factor panel with identical D7a files is a
    # no-op for the whole diagnostics hash.
    shuffled = panel.iloc[::-1].reset_index(drop=True)
    case_panel, _, _ = _capture_run(store, panel=shuffled)
    assert case_a.diagnostics.diagnostics_hash == case_panel.diagnostics.diagnostics_hash
    # (b) permuting the D7a rows changes only the byte-level file hashes;
    # every semantic output is identical.
    store_rev = _Store(tmp_path, name="b")
    store_rev.standard(terminal="A")
    bindings_rev = store_rev.build(reverse=True)
    partition, fold, _ = _partition()
    spec = _spec()
    declaration = _declaration(partition, fold, panel, spec)
    case_b = B.build_development_diagnostics(
        store_rev.root, declaration, spec, panel, partition,
        bindings=bindings_rev,
    )
    for field in (
        "formations", "purged_cells", "formation_accounting", "b", "e_s", "e_l",
        "value_t0", "value_ts", "economic_t0", "economic_ts",
        "accounting_records_hash", "not_assessed",
    ):
        assert getattr(case_a.diagnostics, field) == getattr(case_b, field)


def test_item130_accounting_needs_no_factor_panel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from smart_beta.evaluation import metrics as metrics_module

    def _raise(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("accounting must compute no metric")

    monkeypatch.setattr(metrics_module, "information_coefficient", _raise)
    monkeypatch.setattr(
        metrics_module, "long_short_mean_tstat", _raise
    )
    store = _Store(tmp_path)
    store.standard(terminal="A")
    bindings = store.build()
    partition, fold, _ = _partition()
    panel = _panel(_standard_values())
    spec = _spec()
    declaration = _declaration(partition, fold, panel, spec)
    accounting = B.compute_factor_independent_accounting(
        store.root, declaration, partition, bindings=bindings
    )
    assert accounting.not_assessed is None
    assert accounting.b == pytest.approx(0.25)


def test_item131_no_network_and_no_credential_read(tmp_path: Path) -> None:
    source = Path(B.__file__).read_text()
    assert "os.environ" not in source
    assert "getenv" not in source
    case, _, _ = _capture_run(_terminal_a(tmp_path))
    assert os.environ.get("TIINGO_API_KEY") == CREDENTIAL_SENTINEL
    assert case.diagnostics.not_assessed is None


def test_item132_short_stress_not_a_bound(tmp_path: Path) -> None:
    case, _, _ = _capture_run(_terminal_a(tmp_path))
    record = case.classify()
    assert record.delisting_return_status == B.DELISTING_RETURN_NOT_CERTIFIED
    payload = record.to_dict()
    assert "bound" not in B.canonical_json(payload)
    assert "tau" not in payload


def test_item133_es_undefined_is_not_assessed(tmp_path: Path) -> None:
    store = _Store(tmp_path)
    store.standard()
    empty = pd.DataFrame(
        {
            "date": pd.Series([], dtype="datetime64[ns]"),
            "stock_id": pd.Series([], dtype="string"),
            "value": pd.Series([], dtype="float64"),
        }
    )
    case, _, _ = _capture_run(store, panel=empty)
    assert case.diagnostics.e_s is None
    record = case.classify()
    assert record.status == B.STATUS_NOT_ASSESSED
    assert record.advancement_blocked is True


# END_OF_TESTS
