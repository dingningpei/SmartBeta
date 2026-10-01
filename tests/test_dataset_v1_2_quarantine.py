"""PQA — Dataset-v1.2 P-Q quarantine overlay tests (synthetic fixtures only).

Every test builds a synthetic ``tmp_path`` store with injected
:class:`~smart_beta.dataset_v1.quarantine.Bindings`; none touches the real
``data_store``, ``reserve_sealed``, any credential or any provider.  An
autouse socket guard and credential sentinel mirror the D7c/D8 suites.

Functions are named ``test_itemNN_*`` so the completion report's test-to-item
map is mechanical (items 1-19 of the frozen spec §11).
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
import sys
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd
import pytest

from smart_beta.dataset_v1 import quarantine as Q
from smart_beta.dataset_v1.trusted_inputs import canonical_json as _trusted_canon
from smart_beta.spec.engine import EvidenceClass, TrustedInput
from smart_beta.spec.expression import (
    Add,
    CrossSectional,
    CrossSectionalOp,
    Div,
    Expr,
    Field,
    Lag,
    Literal,
    Mul,
    Rolling,
    RollingOp,
    Sub,
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
from smart_beta.vendors.tiingo.calendar_source import build_nyse_calendar

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CAL = build_nyse_calendar(date(2019, 1, 1), date(2024, 12, 31))
SESSIONS: list[date] = [ts.date() for ts in CAL.dates]
POS = {d: i for i, d in enumerate(SESSIONS)}
CREDENTIAL_SENTINEL = "pqa-credential-sentinel-7c31"
VALID_HASH = "a" * 64

D5_FLAGS = "derived/qc/q-5d1028655f8c82e1/day_flags.parquet"
D4_IDENT = "derived/identity/i-a21f1e46621dee94/identity_assignment.parquet"
D7C_BASE = "derived/trusted_inputs/ti-aa23fabd3db4e595"
D7A_BASE = "derived/returns/r-34565f074d64ccda"
D6_BASE = "derived/universe/u-6150bc6ac2866b7f"

_ARTIFACT_MANIFESTS = {
    "n-f874d3ecb61374d4": (
        "normalized/n-f874d3ecb61374d4/build.json", "normalized_hash"
    ),
    "q-5d1028655f8c82e1": (
        "derived/qc/q-5d1028655f8c82e1/qc_report.json", "qc_hash"
    ),
    "c-27b6dabd0db0cb15": (
        "derived/classification/c-27b6dabd0db0cb15/dsc.json", "dsc_hash"
    ),
    "u-6150bc6ac2866b7f": (
        "derived/universe/u-6150bc6ac2866b7f/universe.json", "universe_hash"
    ),
    "r-34565f074d64ccda": (
        "derived/returns/r-34565f074d64ccda/returns.json", "returns_hash"
    ),
    "ti-aa23fabd3db4e595": (
        "derived/trusted_inputs/ti-aa23fabd3db4e595/trusted_inputs.json",
        "ti_hash",
    ),
}


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_sockets(monkeypatch: pytest.MonkeyPatch):
    def _refuse(*args: object, **kwargs: object) -> None:
        raise RuntimeError("PQA tests must never open a socket")

    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(socket, "create_connection", _refuse)
    yield


@pytest.fixture(autouse=True)
def _credential_sentinel(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TIINGO_API_KEY", CREDENTIAL_SENTINEL)
    yield


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _prev(d: date) -> date | None:
    i = POS.get(d, -1)
    return SESSIONS[i - 1] if i > 0 else None


def _next(d: date) -> date | None:
    i = POS.get(d, -1)
    return SESSIONS[i + 1] if i >= 0 and i + 1 < len(SESSIONS) else None


def _sessions_between(a: date, b: date) -> list[date]:
    return [d for d in SESSIONS if a <= d <= b]


def _canon(obj: object) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _write_parquet(root: Path, rel: str, frame: pd.DataFrame) -> str:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path, engine="pyarrow", index=False, compression="zstd")
    return _sha256_file(path)


def _stage(
    root: Path,
    stage: str,
    artifact_id: str,
    manifest_rel: str,
    field: str,
    files: Sequence[tuple[str, pd.DataFrame]],
) -> Q.StageBinding:
    hashes: dict[str, str] = {}
    for rel, frame in files:
        hashes[rel] = _write_parquet(root, rel, frame)
    manifest = {"files_sha256": hashes}
    manifest[field] = hashlib.sha256(_canon(hashes).encode("utf-8")).hexdigest()
    manifest_path = root / manifest_rel
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_bytes((_canon(manifest) + "\n").encode("utf-8"))
    return Q.StageBinding(
        stage=stage,
        artifact_id=artifact_id,
        manifest_relative_path=manifest_rel,
        manifest_hash_field=field,
        manifest_hash=manifest[field],
        manifest_sha256=_sha256_file(manifest_path),
        files=tuple(Q.FileBinding(rel, sha) for rel, sha in hashes.items()),
    )


def _capabilities(history: int) -> dict[str, DataCapability]:
    common = dict(
        frequency=Frequency.DAILY,
        history=history,
        has_knowledge_date=True,
        has_positive_vintage_identity=False,
        revision_policies=frozenset({RevisionPolicy.POINT_IN_TIME}),
    )
    return {
        Q.INPUT_DAILY_TOTAL_RETURN: DataCapability(
            semantic_id=Q.INPUT_DAILY_TOTAL_RETURN,
            observation_period=ObservationPeriod.PERIOD,
            units=Unit.FRACTION,
            **common,
        ),
        Q.INPUT_TOTAL_RETURN_INDEX: DataCapability(
            semantic_id=Q.INPUT_TOTAL_RETURN_INDEX,
            observation_period=ObservationPeriod.INSTANT,
            units=Unit.RATIO,
            **common,
        ),
        Q.INPUT_DOLLAR_VOLUME: DataCapability(
            semantic_id=Q.INPUT_DOLLAR_VOLUME,
            observation_period=ObservationPeriod.PERIOD,
            units=Unit.CURRENCY,
            **common,
        ),
    }


def _ti(values: pd.DataFrame, semantic: str) -> TrustedInput:
    return TrustedInput(
        values=values,
        capability=_capabilities(values.shape[0])[semantic],
        evidence_class=EvidenceClass.LIVE_RECORDED,
    )


def _requirement(semantic: str) -> DataRequirement:
    units = Unit.RATIO if semantic == Q.INPUT_TOTAL_RETURN_INDEX else None
    period = (
        ObservationPeriod.INSTANT
        if semantic == Q.INPUT_TOTAL_RETURN_INDEX
        else ObservationPeriod.PERIOD
    )
    return DataRequirement(
        semantic_id=semantic,
        frequency=Frequency.DAILY,
        observation_period=period,
        units=units,
        lookback=0,
    )


_ALIAS = {
    Q.INPUT_DAILY_TOTAL_RETURN: "ret",
    Q.INPUT_TOTAL_RETURN_INDEX: "tri",
    Q.INPUT_DOLLAR_VOLUME: "dv",
}


def _spec(expression: object, semantics: Sequence[str]) -> FactorSpec:
    return FactorSpec(
        id="pqa_probe",
        description="PQA calculus probe",
        expression=expression,
        inputs=tuple(
            FactorInput(_ALIAS[s], _requirement(s)) for s in semantics
        ),
        frequency=Frequency.DAILY,
        missing_policy=MissingPolicy.PROPAGATE,
    )


def _overlay(input_states: pd.DataFrame) -> Q.Overlay:
    empty = pd.DataFrame()
    return Q.Overlay(
        flags=empty,
        primitive_states=empty,
        input_states=input_states,
        label_states=empty,
        liquidity_exposure=empty,
        identity={},
    )


def _input_states(rows: Sequence[tuple[str, date, str, date, int]]) -> pd.DataFrame:
    return pd.DataFrame(list(rows), columns=list(Q._INPUT_STATE_COLUMNS))


def _state_map(frame: pd.DataFrame) -> dict[tuple[date, str], int]:
    return {
        (row.date, row.security_key): int(row.state)
        for row in frame.itertuples(index=False)
    }


# ---------------------------------------------------------------------------
# Synthetic store for build/identity tests
# ---------------------------------------------------------------------------


class _Store:
    def __init__(self, tmp_path: Path, name: str = "store"):
        self.root = tmp_path / name
        self.root.mkdir(parents=True, exist_ok=True)
        self.identity: list[tuple] = []
        self.flags: list[tuple] = []
        self.long: dict[str, list[tuple]] = {name: [] for name in Q.INPUT_NAMES}
        self.segments: list[tuple] = []
        self.labels: list[tuple] = []
        self.horizons: list[tuple] = []
        self.members: list[tuple] = []
        self.expected_flag_count: int | None = None

    # -- declarations ----------------------------------------------------

    def key(
        self,
        ticker: str,
        *,
        listed_start: date = date(2019, 1, 2),
        listed_end: date = date(2023, 6, 30),
        security_key: str | None = None,
    ) -> str:
        sk = security_key or f"TIINGO:{ticker}"
        self.identity.append((sk, ticker, listed_start, listed_end, "INTERVAL_ASSIGNED"))
        return sk

    def flag(self, ticker: str, session: date) -> None:
        self.flags.append((ticker, session, "CORP_ACTION_SUSPECT", "UNCERTIFIED_SEMANTICS"))

    def primitive(self, input_name: str, key: str, session: date, value: float = 1.0) -> None:
        row_date = _next(session)
        if row_date is None:
            raise AssertionError("cannot place a primitive on the last session")
        self.long[input_name].append((row_date, key, session, value))

    def segment(self, key: str, anchor: date, last: date, admitted: bool = True) -> None:
        self.segments.append((key, 1, anchor, last, admitted))

    def label(
        self,
        formation: date,
        key: str,
        h: int,
        *,
        endpoint: date,
        observed_end: date | None,
        label_state: str = "OBSERVED_PATH_COMPLETE",
        crosses: bool = False,
    ) -> None:
        self.labels.append((formation, key, h, endpoint, label_state, observed_end))
        self.horizons.append((formation, h, endpoint, crosses))

    def member(self, formation: date, key: str) -> None:
        self.members.append((formation, key))

    # -- materialise -----------------------------------------------------

    def build(self, *, expected_flag_count: int | None = None) -> Q.Bindings:
        root = self.root
        day_flags = pd.DataFrame(
            {
                "ticker": pd.Series([r[0] for r in self.flags], dtype="string"),
                "date": pd.Series([r[1] for r in self.flags], dtype="datetime64[ns]"),
                "class_or_flag": pd.Series([r[2] for r in self.flags], dtype="string"),
                "disposition": pd.Series([r[3] for r in self.flags], dtype="string"),
            }
        )
        identity = pd.DataFrame(
            {
                "security_key": pd.Series([r[0] for r in self.identity], dtype="string"),
                "ticker": pd.Series([r[1] for r in self.identity], dtype="string"),
                "listed_start": pd.Series([r[2] for r in self.identity], dtype="datetime64[ns]"),
                "listed_end": pd.Series([r[3] for r in self.identity], dtype="datetime64[ns]"),
                "state": pd.Series([r[4] for r in self.identity], dtype="string"),
            }
        )
        labels = pd.DataFrame(
            {
                "formation_date": pd.Series([r[0] for r in self.labels], dtype="datetime64[ns]"),
                "security_key": pd.Series([r[1] for r in self.labels], dtype="string"),
                "h": pd.Series([r[2] for r in self.labels], dtype="int64"),
                "endpoint": pd.Series([r[3] for r in self.labels], dtype="datetime64[ns]"),
                "label_state": pd.Series([r[4] for r in self.labels], dtype="string"),
                "observed_segment_end": pd.Series([r[5] for r in self.labels], dtype="datetime64[ns]"),
            }
        )
        members = pd.DataFrame(
            {
                "formation_date": pd.Series([r[0] for r in self.members], dtype="datetime64[ns]"),
                "security_key": pd.Series([r[1] for r in self.members], dtype="string"),
                "listed_end_dev": pd.Series([date(2023, 6, 30)] * len(self.members), dtype="datetime64[ns]"),
            }
        )
        horizons = pd.DataFrame(
            {
                "formation_date": pd.Series([r[0] for r in self.horizons], dtype="datetime64[ns]"),
                "h": pd.Series([r[1] for r in self.horizons], dtype="int64"),
                "endpoint": pd.Series([r[2] for r in self.horizons], dtype="datetime64[ns]"),
                "crosses_reserve": pd.Series([r[3] for r in self.horizons], dtype=bool),
            }
        )
        segments = pd.DataFrame(
            {
                "security_key": pd.Series([r[0] for r in self.segments], dtype="string"),
                "segment_id": pd.Series([r[1] for r in self.segments], dtype="int64"),
                "anchor_session": pd.Series([r[2] for r in self.segments], dtype="datetime64[ns]"),
                "last_session": pd.Series([r[3] for r in self.segments], dtype="datetime64[ns]"),
                "admitted": pd.Series([r[4] for r in self.segments], dtype=bool),
            }
        )
        longs = {}
        for name, rows in self.long.items():
            longs[name] = pd.DataFrame(
                {
                    "row_date": pd.Series([r[0] for r in rows], dtype="datetime64[ns]"),
                    "security_key": pd.Series([r[1] for r in rows], dtype="string"),
                    "source_session": pd.Series([r[2] for r in rows], dtype="datetime64[ns]"),
                    "value": pd.Series([r[3] for r in rows], dtype="float64"),
                }
            )

        d5 = _stage(root, "D5", "q-test", f"{D5_FLAGS.rsplit('/', 1)[0]}/qc_report.json",
                    "qc_hash", [(D5_FLAGS, day_flags)])
        d4 = _stage(root, "D4", "i-test", f"{D4_IDENT.rsplit('/', 1)[0]}/identity.json",
                    "identity_hash", [(D4_IDENT, identity)])
        d7c = _stage(
            root, "D7C", "ti-test", f"{D7C_BASE}/trusted_inputs.json", "ti_hash",
            [
                (f"{D7C_BASE}/daily_total_return.parquet", longs[Q.INPUT_DAILY_TOTAL_RETURN]),
                (f"{D7C_BASE}/dollar_volume.parquet", longs[Q.INPUT_DOLLAR_VOLUME]),
                (f"{D7C_BASE}/total_return_index.parquet", longs[Q.INPUT_TOTAL_RETURN_INDEX]),
                (f"{D7C_BASE}/segments.parquet", segments),
            ],
        )
        d7a = _stage(
            root, "D7A", "r-test", f"{D7A_BASE}/returns.json", "returns_hash",
            [
                (f"{D7A_BASE}/labels.parquet", labels),
                (f"{D7A_BASE}/members.parquet", members),
                (f"{D7A_BASE}/horizons.parquet", horizons),
            ],
        )
        d6 = _stage(
            root, "D6", "u-test", f"{D6_BASE}/universe.json", "universe_hash",
            [(f"{D6_BASE}/universe_development.parquet", members[["formation_date", "security_key"]])],
        )
        count = expected_flag_count if expected_flag_count is not None else len(self.flags)
        bindings = replace(
            Q.DEFAULT_BINDINGS, d4=d4, d5=d5, d6=d6, d7a=d7a, d7c=d7c,
            documents=(), pinned_code=Q._pinned_code(),
            artifact_manifests=_ARTIFACT_MANIFESTS,
            expected_flag_count=count,
        )
        return bindings


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


def _genesis_records() -> tuple[_FakeRecord, ...]:
    return (
        _FakeRecord(
            Q.D8_GENESIS_SEQ,
            "ARTIFACT",
            Q.D8_GENESIS_RECORD_HASH,
            {"source_label": Q.D8_SOURCE_LABEL},
        ),
    )


class _SpyReader:
    def __init__(self, records: Sequence[_FakeRecord]):
        self.records = tuple(records)
        self.calls: list[str] = []

    def __call__(self, path: Any) -> tuple[_FakeRecord, ...]:
        self.calls.append(str(path))
        return self.records


def _identity_context(tmp_path: Path, store: _Store | None = None):
    root = (store.root if store is not None else tmp_path / "store")
    root.mkdir(parents=True, exist_ok=True)
    _write_artifact_manifests(root)
    bindings = replace(
        Q.DEFAULT_BINDINGS,
        artifact_manifests=_ARTIFACT_MANIFESTS,
    )
    payload = Q.build_identity_payload()
    pq_dir = Q._overlay_directory(root, bindings)
    pq_dir.mkdir(parents=True, exist_ok=True)
    (pq_dir / "dataset_identity.json").write_bytes(Q._canonical_bytes(payload))
    return root, bindings, pq_dir, payload


# ===========================================================================
# 1. Q(s): interior / interval boundaries / last development session
# ===========================================================================


def test_item01_flag_sessions_boundaries() -> None:
    key = "TIINGO:AAA"
    day_flags = pd.DataFrame(
        {
            "ticker": ["AAA", "AAA", "AAA", "AAA", "CCC", "DDD"],
            "date": pd.to_datetime(
                [date(2020, 1, 8), date(2019, 1, 2), date(2020, 1, 3),
                 date(2020, 1, 6), date(2023, 6, 30), date(2020, 1, 7)]
            ),
            "class_or_flag": ["CORP_ACTION_SUSPECT"] * 6,
            "disposition": ["x"] * 6,
        }
    )
    identity = pd.DataFrame(
        {
            "security_key": [key, "TIINGO:BBB", "TIINGO:CCC", "TIINGO:DDD"],
            "ticker": ["AAA", "BBB", "CCC", "DDD"],
            "listed_start": pd.to_datetime(
                [date(2019, 1, 2), date(2019, 1, 2), date(2019, 1, 2),
                 date(2020, 1, 7)]
            ),
            "listed_end": pd.to_datetime(
                [date(2020, 1, 6), date(2023, 6, 30), date(2023, 6, 30),
                 date(2020, 1, 31)]
            ),
            "state": ["INTERVAL_ASSIGNED"] * 4,
        }
    )
    flags = Q.compute_flag_panel(day_flags, identity, SESSIONS)
    by_session = {
        row.flag_session: row for row in flags.itertuples(index=False)
    }
    # interior flag: both neighbours
    interior = by_session[date(2020, 1, 3)]
    assert interior.prev_session == date(2020, 1, 2)
    assert interior.next_session == date(2020, 1, 6)
    # Monday flag: the previous trusted session is Friday, not Sunday
    monday = by_session[date(2020, 1, 6)]
    assert monday.prev_session == date(2020, 1, 3)
    # at the interval start: no prev
    assert by_session[date(2019, 1, 2)].prev_session is None
    # a flag at its own interval start has no prev (interval not ignored)
    assert by_session[date(2020, 1, 7)].prev_session is None
    # at the interval end: no next inside the interval
    assert by_session[date(2020, 1, 6)].next_session is None
    # last development session: next is reserve, flagged as beyond development
    last = by_session[date(2023, 6, 30)]
    assert last.next_session is None
    assert bool(last.next_beyond_development) is True


def test_item02_daily_return_states_exact() -> None:
    key = "K1"
    s = date(2020, 3, 10)
    prev_s = _prev(s)
    next_s = _next(s)
    flags = pd.DataFrame(
        [(key, s, prev_s, next_s, False)],
        columns=["security_key", "flag_session", "prev_session", "next_session",
                 "next_beyond_development"],
    )
    present = {
        Q.INPUT_DAILY_TOTAL_RETURN: {
            (key, _prev(prev_s)), (key, prev_s), (key, s), (key, next_s),
            (key, _next(next_s)), (key, _prev(prev_s)),
        },
        Q.INPUT_DOLLAR_VOLUME: set(),
        Q.INPUT_TOTAL_RETURN_INDEX: set(),
    }
    states = Q.compute_primitive_states(flags, present=present, tri_segments=[])
    daily = {
        row.session: int(row.state)
        for row in states[states.primitive == Q.INPUT_DAILY_TOTAL_RETURN].itertuples(index=False)
    }
    assert daily[prev_s] == Q.STATE_QUARANTINED
    assert daily[s] == Q.STATE_QUARANTINED
    assert daily[next_s] == Q.STATE_QUARANTINED
    assert _prev(prev_s) not in daily
    assert _next(next_s) not in daily


def test_item03_dollar_volume_states_exact() -> None:
    key = "K1"
    s = date(2020, 3, 10)
    prev_s = _prev(s)
    next_s = _next(s)
    flags = pd.DataFrame(
        [(key, s, prev_s, next_s, False)],
        columns=["security_key", "flag_session", "prev_session", "next_session",
                 "next_beyond_development"],
    )
    present = {
        Q.INPUT_DAILY_TOTAL_RETURN: set(),
        Q.INPUT_DOLLAR_VOLUME: {(key, prev_s), (key, s), (key, next_s)},
        Q.INPUT_TOTAL_RETURN_INDEX: set(),
    }
    states = Q.compute_primitive_states(flags, present=present, tri_segments=[])
    dv = {
        row.session: int(row.state)
        for row in states[states.primitive == Q.INPUT_DOLLAR_VOLUME].itertuples(index=False)
    }
    assert dv[prev_s] == Q.STATE_QUARANTINED
    assert dv[s] == Q.STATE_QUARANTINED
    assert next_s not in dv


def test_item04_tri_propagation() -> None:
    key = "K1"
    anchor = date(2020, 1, 2)
    seg_end = date(2020, 2, 28)
    s = date(2020, 1, 15)
    prev_s = _prev(s)
    sessions = _sessions_between(anchor, seg_end)
    present = {
        Q.INPUT_DAILY_TOTAL_RETURN: {(key, prev_s), (key, s)},
        Q.INPUT_DOLLAR_VOLUME: set(),
        Q.INPUT_TOTAL_RETURN_INDEX: {(key, d) for d in sessions},
    }
    flags = pd.DataFrame(
        [(key, s, prev_s, _next(s), False)],
        columns=["security_key", "flag_session", "prev_session", "next_session",
                 "next_beyond_development"],
    )
    segment = {
        "security_key": key, "anchor_session": anchor, "last_session": seg_end,
        "admitted": True,
    }
    states = Q.compute_primitive_states(
        flags, present=present, tri_segments=[segment]
    )
    tri = {
        row.session: int(row.state)
        for row in states[states.primitive == Q.INPUT_TOTAL_RETURN_INDEX].itertuples(index=False)
    }
    assert anchor not in tri  # the anchor stays CLEAN
    assert tri[prev_s] == Q.STATE_PROPAGATED
    assert all(tri[d] == Q.STATE_PROPAGATED for d in sessions if d >= prev_s)
    assert all(d not in tri for d in sessions if d < prev_s and d != anchor)
    # a non-admitted segment never propagates
    nonadmitted = {"security_key": key, "anchor_session": anchor,
                   "last_session": seg_end, "admitted": False}
    states2 = Q.compute_primitive_states(
        flags, present=present, tri_segments=[nonadmitted]
    )
    assert states2[states2.primitive == Q.INPUT_TOTAL_RETURN_INDEX].empty


def test_item05_absent_cells_not_applicable() -> None:
    key = "K1"
    s = date(2020, 3, 10)
    prev_s = _prev(s)
    # prev_s is in Q(s) but the D7c cell is absent -> it must not be listed.
    flags = pd.DataFrame(
        [(key, s, prev_s, _next(s), False)],
        columns=["security_key", "flag_session", "prev_session", "next_session",
                 "next_beyond_development"],
    )
    present = {
        Q.INPUT_DAILY_TOTAL_RETURN: {(key, s)},
        Q.INPUT_DOLLAR_VOLUME: set(),
        Q.INPUT_TOTAL_RETURN_INDEX: set(),
    }
    states = Q.compute_primitive_states(flags, present=present, tri_segments=[])
    listed = {
        row.session
        for row in states[states.primitive == Q.INPUT_DAILY_TOTAL_RETURN].itertuples(index=False)
    }
    assert prev_s not in listed
    assert s in listed
    # The calculus reports 3 (NOT_APPLICABLE) for the absent, quarantined cell.
    grid = pd.DatetimeIndex([pd.Timestamp(s)])
    values = pd.DataFrame({key: [np.nan]}, index=grid)
    overlay = _overlay(_input_states([]))
    spec = _spec(Field("ret"), [Q.INPUT_DAILY_TOTAL_RETURN])
    out = Q.factor_input_states(
        spec, overlay=overlay, inputs={Q.INPUT_DAILY_TOTAL_RETURN: _ti(values, Q.INPUT_DAILY_TOTAL_RETURN)}
    )
    assert int(out.iloc[0].state) == Q.STATE_NOT_APPLICABLE


# ===========================================================================
# 6. Lag alignment
# ===========================================================================


def test_item06_lag_alignment_no_double_lag() -> None:
    key = "K1"
    s = date(2020, 3, 10)
    prev_s = _prev(s)
    next_s = _next(s)
    next_next = _next(next_s)
    flags = pd.DataFrame(
        [(key, s, prev_s, next_s, False)],
        columns=["security_key", "flag_session", "prev_session", "next_session",
                 "next_beyond_development"],
    )
    present = {
        Q.INPUT_DAILY_TOTAL_RETURN: set(),
        Q.INPUT_DOLLAR_VOLUME: {(key, prev_s), (key, s), (key, next_s), (key, next_next)},
        Q.INPUT_TOTAL_RETURN_INDEX: set(),
    }
    primitive = Q.compute_primitive_states(flags, present=present, tri_segments=[])
    long_rows = {
        Q.INPUT_DOLLAR_VOLUME: pd.DataFrame(
            [
                (next_s, key, s, 1.0),
                (next_next, key, next_s, 1.0),
            ],
            columns=["row_date", "security_key", "source_session", "value"],
        )
    }
    input_states = Q.compute_input_states(flags, primitive, long_rows=long_rows)
    listed = {
        (row.row_date, row.security_key): int(row.state)
        for row in input_states.itertuples(index=False)
    }
    assert listed[(next_s, key)] == Q.STATE_QUARANTINED
    assert (next_next, key) not in listed  # no double lag


# ===========================================================================
# 7. NaN-shadow vs AST oracle
# ===========================================================================


def _probe_grid() -> pd.DatetimeIndex:
    return pd.DatetimeIndex(
        [pd.Timestamp(d) for d in _sessions_between(date(2020, 1, 2), date(2020, 3, 31))]
    )


def _probe_inputs(grid: pd.DatetimeIndex) -> tuple[dict[str, TrustedInput], Q.Overlay]:
    keys = ["AAA", "BBB", "CCC", "DDD"]
    rng = np.random.default_rng(7)
    ret = pd.DataFrame(
        rng.normal(0.0, 0.01, size=(len(grid), len(keys))), index=grid, columns=keys
    )
    tri = (1.0 + ret).cumprod()
    dv = pd.DataFrame(
        rng.uniform(1e6, 5e6, size=(len(grid), len(keys))), index=grid, columns=keys
    )
    ret.iloc[3, 0] = np.nan
    tri.iloc[4, 1] = np.nan
    dv.iloc[5, 2] = np.nan
    inputs = {
        Q.INPUT_DAILY_TOTAL_RETURN: _ti(ret, Q.INPUT_DAILY_TOTAL_RETURN),
        Q.INPUT_TOTAL_RETURN_INDEX: _ti(tri, Q.INPUT_TOTAL_RETURN_INDEX),
        Q.INPUT_DOLLAR_VOLUME: _ti(dv, Q.INPUT_DOLLAR_VOLUME),
    }
    rows = [
        (Q.INPUT_DAILY_TOTAL_RETURN, grid[6].date(), "AAA", grid[5].date(), 1),
        (Q.INPUT_DAILY_TOTAL_RETURN, grid[8].date(), "BBB", grid[7].date(), 2),
        (Q.INPUT_TOTAL_RETURN_INDEX, grid[7].date(), "CCC", grid[6].date(), 2),
        (Q.INPUT_DOLLAR_VOLUME, grid[9].date(), "DDD", grid[8].date(), 1),
    ]
    return inputs, _overlay(_input_states(rows))


PROBE_EXPRESSIONS = [
    ("field", lambda: Field("ret"), [Q.INPUT_DAILY_TOTAL_RETURN]),
    ("lag", lambda: Lag(Field("ret"), 2), [Q.INPUT_DAILY_TOTAL_RETURN]),
    ("rolling_mean", lambda: Rolling(RollingOp.MEAN, Field("ret"), 3), [Q.INPUT_DAILY_TOTAL_RETURN]),
    ("rolling_sum", lambda: Rolling(RollingOp.SUM, Field("dv"), 5), [Q.INPUT_DOLLAR_VOLUME]),
    ("rolling_std", lambda: Rolling(RollingOp.STD, Field("ret"), 4), [Q.INPUT_DAILY_TOTAL_RETURN]),
    ("rolling_min", lambda: Rolling(RollingOp.MIN, Field("dv"), 3), [Q.INPUT_DOLLAR_VOLUME]),
    ("rolling_max", lambda: Rolling(RollingOp.MAX, Field("dv"), 3), [Q.INPUT_DOLLAR_VOLUME]),
    ("add", lambda: Add(Field("ret"), Literal(1.0)), [Q.INPUT_DAILY_TOTAL_RETURN]),
    ("sub", lambda: Sub(Field("tri"), Literal(0.5)), [Q.INPUT_TOTAL_RETURN_INDEX]),
    ("mul", lambda: Mul(Field("ret"), Field("dv")), [Q.INPUT_DAILY_TOTAL_RETURN, Q.INPUT_DOLLAR_VOLUME]),
    ("div", lambda: Div(Field("tri"), Lag(Field("tri"), 1)), [Q.INPUT_TOTAL_RETURN_INDEX]),
    ("rank", lambda: CrossSectional(CrossSectionalOp.RANK, Field("ret")), [Q.INPUT_DAILY_TOTAL_RETURN]),
    (
        "winsorize",
        lambda: CrossSectional(CrossSectionalOp.WINSORIZE, Field("ret"), bounds=(0.1, 0.9)),
        [Q.INPUT_DAILY_TOTAL_RETURN],
    ),
    (
        "standardize",
        lambda: CrossSectional(CrossSectionalOp.STANDARDIZE, Field("ret")),
        [Q.INPUT_DAILY_TOTAL_RETURN],
    ),
    (
        "nested",
        lambda: Add(Lag(Field("ret"), 2), Rolling(RollingOp.MEAN, Field("dv"), 3)),
        [Q.INPUT_DAILY_TOTAL_RETURN, Q.INPUT_DOLLAR_VOLUME],
    ),
    (
        "cs_of_lag",
        lambda: CrossSectional(CrossSectionalOp.RANK, Lag(Field("ret"), 1)),
        [Q.INPUT_DAILY_TOTAL_RETURN],
    ),
    ("literal", lambda: Sub(Literal(5.0), Field("ret")), [Q.INPUT_DAILY_TOTAL_RETURN]),
]


@pytest.mark.parametrize("name,factory,semantics", PROBE_EXPRESSIONS, ids=[p[0] for p in PROBE_EXPRESSIONS])
def test_item07_nan_shadow_matches_oracle(name, factory, semantics) -> None:
    grid = _probe_grid()
    inputs, overlay = _probe_inputs(grid)
    spec = _spec(factory(), semantics)
    shadow = Q.factor_input_states(spec, overlay=overlay, inputs=inputs)
    oracle = Q.oracle_factor_input_states(spec, overlay=overlay, inputs=inputs)
    shadow_map = _state_map(shadow)
    oracle_map = _state_map(oracle)
    assert shadow_map.keys() == oracle_map.keys()
    for cell, state in shadow_map.items():
        assert state == oracle_map[cell], (name, cell, state, oracle_map[cell])


# ===========================================================================
# 8-9. Cross-sectional own-cell rule, residual influence, value invariance
# ===========================================================================


def test_item08_cross_sectional_own_cell_and_residual() -> None:
    grid = _probe_grid()
    keys = ["AAA", "BBB", "CCC", "DDD"]
    ret = pd.DataFrame(
        np.arange(len(grid) * len(keys), dtype=float).reshape(len(grid), len(keys)),
        index=grid,
        columns=keys,
    )
    overlay = _overlay(
        _input_states(
            [(Q.INPUT_DAILY_TOTAL_RETURN, grid[5].date(), "AAA", grid[4].date(), 1)]
        )
    )
    spec = _spec(
        CrossSectional(CrossSectionalOp.RANK, Field("ret")),
        [Q.INPUT_DAILY_TOTAL_RETURN],
    )
    out = Q.factor_input_states(
        spec, overlay=overlay,
        inputs={Q.INPUT_DAILY_TOTAL_RETURN: _ti(ret, Q.INPUT_DAILY_TOTAL_RETURN)},
    )
    states = _state_map(out)
    assert states[(grid[5].date(), "AAA")] == Q.STATE_PROPAGATED
    for key in ("BBB", "CCC", "DDD"):
        assert states[(grid[5].date(), key)] == Q.STATE_CLEAN
    assert out.attrs["residual_cs_cells"][grid[5].date()] >= 1


def test_item09_factor_values_unchanged() -> None:
    grid = _probe_grid()
    inputs, overlay = _probe_inputs(grid)
    spec = _spec(
        Add(Field("ret"), Rolling(RollingOp.MEAN, Field("ret"), 3)),
        [Q.INPUT_DAILY_TOTAL_RETURN],
    )
    original = Q._evaluate_wide(spec, inputs)
    before = {name: trusted.values.copy() for name, trusted in inputs.items()}
    out = Q.factor_input_states(spec, overlay=overlay, inputs=inputs)
    returned = out.attrs["values"]
    pd.testing.assert_frame_equal(returned, original)
    # The shadow never leaks into the caller's trusted inputs.
    for name, trusted in inputs.items():
        pd.testing.assert_frame_equal(trusted.values, before[name])


# ===========================================================================
# 10. Label rules
# ===========================================================================


def test_item10_label_rules() -> None:
    key = "K1"
    f = date(2020, 1, 2)
    e = date(2020, 2, 3)
    sessions = _sessions_between(date(2019, 12, 1), date(2020, 3, 1))

    def labels_for(flag_session: date, prev_session: date | None):
        flags = pd.DataFrame(
            [(key, flag_session, prev_session, _next(flag_session), False)],
            columns=["security_key", "flag_session", "prev_session", "next_session",
                     "next_beyond_development"],
        )
        labels = pd.DataFrame(
            [(f, key, 21, e, "OBSERVED_PATH_COMPLETE", e)],
            columns=["formation_date", "security_key", "h", "endpoint", "label_state",
                     "observed_segment_end"],
        )
        horizons = pd.DataFrame(
            [(f, 21, e, False)],
            columns=["formation_date", "h", "endpoint", "crosses_reserve"],
        )
        return Q.compute_label_states(labels, horizons, flags, sessions=sessions)

    # s in (f, e'] -> FLAG_IN_PATH
    out = labels_for(date(2020, 1, 15), date(2020, 1, 14))
    assert out.iloc[0].reason == "FLAG_IN_PATH"
    # s == f is excluded; prev(s) < f -> CLEAN (no row)
    clean = labels_for(f, _prev(f))
    assert clean.empty
    # s == e' is included
    boundary = labels_for(e, _prev(e))
    assert boundary.iloc[0].reason == "FLAG_IN_PATH"
    # prev(s) in [f, e'] while s is outside (f, e'] -> ENDPOINT_OR_FORMATION_CLOSE
    prev_cond = labels_for(_next(e), e)
    assert prev_cond.iloc[0].reason == "ENDPOINT_OR_FORMATION_CLOSE"


def test_item10b_label_reserve_and_empty_path() -> None:
    key = "K1"
    f = date(2023, 5, 31)
    e = date(2023, 6, 30)
    sessions = _sessions_between(date(2023, 1, 1), date(2023, 6, 30))
    flags = pd.DataFrame(
        columns=["security_key", "flag_session", "prev_session", "next_session",
                 "next_beyond_development"],
    )
    labels = pd.DataFrame(
        [(f, key, 21, e, "OBSERVED_PATH_COMPLETE", e)],
        columns=["formation_date", "security_key", "h", "endpoint", "label_state",
                 "observed_segment_end"],
    )
    horizons = pd.DataFrame(
        [(f, 21, e, False)],
        columns=["formation_date", "h", "endpoint", "crosses_reserve"],
    )
    out = Q.compute_label_states(labels, horizons, flags, sessions=sessions)
    assert out.iloc[0].reason == "UNDETERMINABLE_RESERVE_BOUNDARY"
    # reserve-crossing horizon keeps NOT_APPLICABLE (no row)
    horizons2 = pd.DataFrame(
        [(f, 21, e, True)],
        columns=["formation_date", "h", "endpoint", "crosses_reserve"],
    )
    assert Q.compute_label_states(labels, horizons2, flags, sessions=sessions).empty
    # empty-path identity: e' = f, quarantined iff prev(s) == f
    f2 = date(2020, 1, 2)
    empty_labels = pd.DataFrame(
        [(f2, key, 21, date(2020, 2, 3), "EMPTY_OBSERVED_PATH_IDENTITY", None)],
        columns=["formation_date", "security_key", "h", "endpoint", "label_state",
                 "observed_segment_end"],
    )
    empty_h = pd.DataFrame(
        [(f2, 21, date(2020, 2, 3), False)],
        columns=["formation_date", "h", "endpoint", "crosses_reserve"],
    )
    flag = pd.DataFrame(
        [(key, _next(f2), f2, _next(_next(f2)), False)],
        columns=["security_key", "flag_session", "prev_session", "next_session",
                 "next_beyond_development"],
    )
    sessions2 = _sessions_between(date(2019, 12, 1), date(2020, 3, 1))
    empty_out = Q.compute_label_states(empty_labels, empty_h, flag, sessions=sessions2)
    assert empty_out.iloc[0].reason == "ENDPOINT_OR_FORMATION_CLOSE"


# ===========================================================================
# 11. Liquidity exposure
# ===========================================================================


def test_item11_liquidity_exposure_report_only() -> None:
    key = "K1"
    f = date(2020, 3, 2)
    prev_s = _prev(f)
    flags = pd.DataFrame(
        [(key, prev_s, _prev(prev_s), f, False)],
        columns=["security_key", "flag_session", "prev_session", "next_session",
                 "next_beyond_development"],
    )
    members = pd.DataFrame(
        [(f, key), (f, "K2")], columns=["formation_date", "security_key"]
    )
    before = members.copy(deep=True)
    out = Q.compute_liquidity_exposure(
        members, flags, session_pos=POS, sessions=SESSIONS
    )
    assert list(out.columns) == list(Q._LIQUIDITY_COLUMNS)
    assert len(out) == 2
    by_key = {row.security_key: bool(row.liquidity_window_exposed) for row in out.itertuples(index=False)}
    assert by_key[key] is True
    assert by_key["K2"] is False
    pd.testing.assert_frame_equal(members, before)
    # The API exposes no membership mutation.
    assert not hasattr(Q, "filter_members")
    assert not hasattr(Q, "recompute_universe")


# ===========================================================================
# 12. Resolver firewall
# ===========================================================================


def test_item12_resolver_rejects_reserve_and_paths(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    resolver = Q.QuarantineResolver(root, Q.DEFAULT_BINDINGS)
    for forbidden in ("reserve_sealed/x.parquet", "raw/x.parquet", "dsc_evidence/x.parquet"):
        with pytest.raises(Q.ReserveFirewallError):
            resolver.resolve(forbidden)
    with pytest.raises(Q.ReserveFirewallError):
        resolver.resolve("../escape.parquet")
    with pytest.raises(Q.ReserveFirewallError):
        resolver.resolve("not/allowlisted.parquet")
    # an allowlisted reserve path is still rejected
    reserve_bindings = replace(
        Q.DEFAULT_BINDINGS,
        d5=replace(
            Q.DEFAULT_BINDINGS.d5,
            files=(Q.FileBinding("reserve_sealed/x.parquet", ""),),
        ),
    )
    with pytest.raises(Q.ReserveFirewallError):
        Q.QuarantineResolver(root, reserve_bindings).resolve(
            "reserve_sealed/x.parquet"
        )
    # symlink escape
    outside = tmp_path / "outside.parquet"
    outside.write_bytes(b"x")
    link = root / "link.parquet"
    link.symlink_to(outside)
    bindings = replace(
        Q.DEFAULT_BINDINGS,
        d5=replace(
            Q.DEFAULT_BINDINGS.d5,
            files=(Q.FileBinding("link.parquet", ""),),
        ),
    )
    escaping = Q.QuarantineResolver(root, bindings)
    with pytest.raises(Q.ReserveFirewallError):
        escaping.resolve("link.parquet")
    # post-load date assertion
    with pytest.raises(Q.ReserveFirewallError):
        Q._assert_dates_development(
            pd.DataFrame({"date": [date(2023, 7, 3)]}), ["date"]
        )


# ===========================================================================
# 13. Identity record and mutations
# ===========================================================================


def test_item13_identity_record_and_mutations(tmp_path: Path) -> None:
    root, bindings, pq_dir, payload = _identity_context(tmp_path)
    reader = _SpyReader(_genesis_records())
    identity_hash = Q.verify_dataset_v1_2_identity(
        root, bindings=bindings, read_records_fn=reader
    )
    assert identity_hash == Q.compute_identity_hash(payload)
    # canonical bytes are stable
    assert Q._canonical_bytes(payload) == (Q.canonical_json(payload) + "\n").encode()
    assert Q.compute_identity_hash(payload) == Q.compute_identity_hash(payload)

    def rewrite(mutate) -> None:
        candidate = json.loads(Q.canonical_json(payload))
        mutate(candidate)
        (pq_dir / "dataset_identity.json").write_bytes(Q._canonical_bytes(candidate))

    mutations = [
        lambda p: p.__setitem__("dataset_id", "discovery_dataset_v1_1"),
        lambda p: p.__setitem__("quarantine_rule_hash", "0" * 64),
        lambda p: p["amendment"].__setitem__("sha256", "0" * 64),
        lambda p: p["parent_authority"].__setitem__(
            PARENT_KEY, "0" * 64
        ),
        lambda p: p["inherited_artifacts"].__setitem__("q-5d1028655f8c82e1", "0" * 64),
        lambda p: p["d8"].__setitem__("genesis_record_hash", "0" * 64),
        lambda p: p["d8"].__setitem__("source_label", "wrong"),
    ]
    for mutate in mutations:
        rewrite(mutate)
        with pytest.raises(Q.DatasetIdentityError):
            Q.verify_dataset_v1_2_identity(
                root, bindings=bindings, read_records_fn=reader
            )
    # a K-log record mentioning discovery_dataset_v1_2 fails closed
    tainted = _SpyReader(
        _genesis_records()
        + (_FakeRecord(1, "ARTIFACT", "b" * 64, {"note": Q.DATASET_ID}),)
    )
    (pq_dir / "dataset_identity.json").write_bytes(Q._canonical_bytes(payload))
    with pytest.raises(Q.DatasetIdentityError):
        Q.verify_dataset_v1_2_identity(
            root, bindings=bindings, read_records_fn=tainted
        )


PARENT_KEY = "docs/discovery_dataset_v1_contract.md"


# ===========================================================================
# 14. Knowledge-PIT read-only
# ===========================================================================


def test_item14_klog_read_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, bindings, pq_dir, _ = _identity_context(tmp_path)
    reader = _SpyReader(_genesis_records())

    def _explode(*args: object, **kwargs: object) -> None:
        raise AssertionError("a KnowledgeLog writer must never be constructed")

    monkeypatch.setattr(Q._knowledge, "KnowledgeLog", _explode)
    Q.verify_dataset_v1_2_identity(root, bindings=bindings, read_records_fn=reader)
    assert len(reader.calls) == 1
    assert reader.calls[0].endswith("knowledge_pit/knowledge.jsonl")


# ===========================================================================
# 15. Determinism
# ===========================================================================


def _build_store(tmp_path: Path, name: str, *, flag_session: date) -> _Store:
    store = _Store(tmp_path, name)
    key = store.key("AAA")
    store.flag("AAA", flag_session)
    store.segment(key, date(2019, 1, 2), date(2023, 6, 30), True)
    for session in _sessions_between(date(2020, 1, 2), date(2020, 1, 10)):
        store.primitive(Q.INPUT_DAILY_TOTAL_RETURN, key, session)
        store.primitive(Q.INPUT_DOLLAR_VOLUME, key, session)
        store.primitive(Q.INPUT_TOTAL_RETURN_INDEX, key, session)
    store.label(date(2020, 1, 2), key, 21, endpoint=date(2020, 2, 3),
                observed_end=date(2020, 2, 3))
    store.member(date(2020, 1, 2), key)
    _write_artifact_manifests(store.root)
    return store


def test_item15_determinism(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifests: dict[str, dict[str, Any]] = {}
    for name, tz in (("a", "UTC"), ("b", "Asia/Tokyo")):
        store = _build_store(tmp_path, name, flag_session=date(2020, 1, 6))
        monkeypatch.setenv("TZ", tz)
        monkeypatch.setenv("LC_ALL", "C")
        manifests[name] = Q.build_overlay(store.root, bindings=store.build())
    dir_a = tmp_path / "a" / "derived" / "quarantine" / manifests["a"]["pq_id"]
    dir_b = tmp_path / "b" / "derived" / "quarantine" / manifests["b"]["pq_id"]
    files = ["flags.parquet", "primitive_states.parquet", "input_states.parquet",
             "label_states.parquet", "liquidity_exposure.parquet",
             "dataset_identity.json", "pq.json"]
    for filename in files:
        assert (dir_a / filename).read_bytes() == (dir_b / filename).read_bytes()
    # rebuild is a no-op
    store = _build_store(tmp_path, "a", flag_session=date(2020, 1, 6))
    first = Q.build_overlay(store.root, bindings=store.build())
    second = Q.build_overlay(store.root, bindings=store.build())
    assert first["pq_hash"] == second["pq_hash"]
    # a differing existing directory raises
    (dir_a / "pq.json").write_bytes(b'{"pq_hash":"deadbeef"}\n')
    with pytest.raises(Q.NondeterministicOverlayError):
        Q.build_overlay(store.root, bindings=store.build())


def _bindings_for(root: Path) -> Q.Bindings:
    store = _Store(root.parent, root.name)
    return store.build()


def _bindings_for(root: Path) -> Q.Bindings:
    store = _Store(root.parent, root.name)
    return store.build()


# ===========================================================================
# 16. Pinned-code and document hash checks
# ===========================================================================


def test_item16_pinned_hash_checks(tmp_path: Path) -> None:
    bindings = replace(
        Q.DEFAULT_BINDINGS,
        pinned_code=(Q.HashBinding("pinned", "smart_beta/spec/engine.py", "0" * 64),),
    )
    with pytest.raises(Q.QuarantineBindingError):
        Q._verify_pinned_code(bindings, Q._REPO_ROOT)
    docs = replace(
        Q.DEFAULT_BINDINGS,
        documents=(Q.HashBinding("amendment", Q.AMENDMENT_PATH, "0" * 64),),
    )
    with pytest.raises(Q.QuarantineBindingError):
        Q._verify_documents(docs, Q._REPO_ROOT)
    with pytest.raises(Q.QuarantineBindingError):
        Q._verify_parent_authority(tmp_path)


# ===========================================================================
# 17. Flag-count guard
# ===========================================================================


def test_item17_flag_count_guard(tmp_path: Path) -> None:
    store = _build_store(tmp_path, "guard", flag_session=date(2020, 1, 6))
    store.build()
    with pytest.raises(Q.QuarantineInputError):
        Q.build_overlay(store.root, bindings=store.build(expected_flag_count=999))


# ===========================================================================
# 18. No timestamps or absolute paths
# ===========================================================================


def test_item18_no_timestamps_or_paths(tmp_path: Path) -> None:
    import re

    store = _build_store(tmp_path, "scan", flag_session=date(2020, 1, 6))
    manifest = Q.build_overlay(store.root, bindings=store.build())
    pq_dir = Q._overlay_directory(store.root, store.build())
    pattern = re.compile(rb"\d{4}-\d{2}-\d{2}T")
    scanned = list(manifest["files_sha256"]) + ["pq.json"]
    for filename in scanned:
        blob = (pq_dir / filename).read_bytes()
        assert str(store.root).encode() not in blob
        if filename.endswith(".json"):
            assert pattern.search(blob) is None, filename
    assert manifest["scope"] == "DEVELOPMENT_ONLY"
    # Outputs are deterministically sorted on their key columns.
    flags = pd.read_parquet(pq_dir / "flags.parquet")
    assert flags[["security_key", "flag_session"]].apply(tuple, axis=1).is_monotonic_increasing
    primitive = pd.read_parquet(pq_dir / "primitive_states.parquet")
    assert primitive[["primitive", "security_key", "session"]].apply(tuple, axis=1).is_monotonic_increasing
    input_states = pd.read_parquet(pq_dir / "input_states.parquet")
    assert input_states[["input", "row_date", "security_key"]].apply(tuple, axis=1).is_monotonic_increasing
    labels = pd.read_parquet(pq_dir / "label_states.parquet")
    if len(labels):
        assert labels[["formation_date", "security_key", "h"]].apply(tuple, axis=1).is_monotonic_increasing
    liquidity = pd.read_parquet(pq_dir / "liquidity_exposure.parquet")
    assert liquidity[["formation_date", "security_key"]].apply(tuple, axis=1).is_monotonic_increasing


# ===========================================================================
# 19. Sparse completeness
# ===========================================================================


def test_item19_sparse_completeness(tmp_path: Path) -> None:
    store = _build_store(tmp_path, "sparse", flag_session=date(2020, 1, 6))
    bindings = store.build()
    manifest = Q.build_overlay(store.root, bindings=bindings)
    resolver = Q.QuarantineResolver(store.root, bindings)
    pq_dir = Q._overlay_directory(store.root, bindings)
    input_states = pd.read_parquet(pq_dir / "input_states.parquet")
    primitive = pd.read_parquet(pq_dir / "primitive_states.parquet")
    file_index = {
        Q.INPUT_DAILY_TOTAL_RETURN: 0,
        Q.INPUT_DOLLAR_VOLUME: 1,
        Q.INPUT_TOTAL_RETURN_INDEX: 2,
    }
    for name in Q.INPUT_NAMES:
        long = resolver.read_parquet(
            bindings.d7c.files[file_index[name]].relative_path,
            columns=["row_date", "security_key", "source_session", "value"],
        )
        present = long[long["value"].notna()]
        listed = input_states[input_states["input"] == name]
        # every listed row corresponds to a present D7c row with source_session
        def _cells(frame):
            return {
                (Q._to_date(d), key)
                for d, key in zip(frame["row_date"].tolist(), frame["security_key"].tolist())
            }

        present_cells = _cells(present)
        listed_cells = _cells(listed)
        assert listed_cells <= present_cells
        assert len(listed) <= len(present)
        if len(listed):
            assert set(listed["state"].tolist()) <= {Q.STATE_QUARANTINED, Q.STATE_PROPAGATED}
    assert manifest["counts"]["input_states"] == int(len(input_states))
    assert manifest["counts"]["q_elements"] == int(len(primitive))


def test_mutation_fixture_contract() -> None:
    path = Q._REPO_ROOT / "tests/fixtures/dataset_v1_2/pqa/mutations.json"
    mutations = json.loads(path.read_text(encoding="utf-8"))
    assert len(mutations) == 40
    assert [m["id"] for m in mutations] == [f"A{i}" for i in range(1, 41)]
    for mutation in mutations:
        assert mutation["edits"], mutation["id"]
        assert mutation["file"] == "smart_beta/dataset_v1/quarantine.py"
        for edit in mutation["edits"]:
            assert edit["old"] and edit["new"] is not None, mutation["id"]
        assert hasattr(sys.modules[__name__], mutation["test"]), mutation["id"]


def test_item19b_source_session_assertion(tmp_path: Path) -> None:
    store = _build_store(tmp_path, "source", flag_session=date(2020, 1, 6))
    # Corrupt one long row's source_session.
    rows = store.long[Q.INPUT_DAILY_TOTAL_RETURN]
    row_date, key, _session, value = rows[0]
    rows[0] = (row_date, key, date(2019, 1, 2), value)
    with pytest.raises(Q.QuarantineConsistencyError):
        Q.build_overlay(store.root, bindings=store.build())


def test_item07b_unsupported_node() -> None:
    grid = _probe_grid()
    inputs, overlay = _probe_inputs(grid)
    spec = _spec(Field("ret"), [Q.INPUT_DAILY_TOTAL_RETURN])
    template = pd.DataFrame(np.nan, index=grid, columns=["AAA", "BBB", "CCC", "DDD"])

    class _Unsupported(Expr):
        KIND = "unsupported"

        def children(self):
            return ()

        def to_dict(self):
            return {"op": "unsupported"}

    with pytest.raises(Q.UnsupportedQuarantineNode):
        Q._oracle_node(_Unsupported(), spec, overlay, inputs, template)
