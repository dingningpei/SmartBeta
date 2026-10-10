"""PQ2-B — P-Q v2 source-set tests (synthetic fixtures only).

Every test is offline and synthetic: an autouse socket guard and credential
sentinel mirror the rest of the dataset-v1.2 suites.  The D3/D4/D7A files are
tiny parquet fixtures under ``tmp_path`` with injected bindings, exactly as the
D7a/D7b suites do; no real ``data_store`` artifact, reserve, holdout, ``raw/``,
``dsc_evidence`` or K-PIT path is ever opened.

The mutation fixture (``tests/fixtures/pqv2/labels/mutations.json``) targets one
named test per mutation; the mapping is checked mechanically by the label
suite's ``test_mutation_fixture_contract``.
"""

from __future__ import annotations

import builtins
import hashlib
import json
import socket
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from smart_beta.dataset_v1 import delisting_bridge as B
from smart_beta.dataset_v1 import pqv2_core as C
from smart_beta.dataset_v1 import pqv2_sources as S
from smart_beta.dataset_v1 import quarantine as Q
from smart_beta.dataset_v1 import returns as R
from smart_beta.spec.engine import EvidenceClass, TrustedInput
from smart_beta.spec.requirements import (
    DataCapability,
    Frequency,
    ObservationPeriod,
    RevisionPolicy,
    Unit,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
CREDENTIAL_SENTINEL = "pqv2-sources-credential-sentinel-7b2e"
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
        raise RuntimeError("PQ2-B source tests must never open a socket")

    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(socket, "create_connection", _refuse)
    yield


@pytest.fixture(autouse=True)
def _credential_sentinel(monkeypatch: pytest.MonkeyPatch):
    for key in _CREDENTIAL_KEYS:
        monkeypatch.setenv(key, CREDENTIAL_SENTINEL)
    yield


def test_credential_sentinel_never_leaks() -> None:
    assert C.SourceState.UNKNOWN_RESERVE != CREDENTIAL_SENTINEL


# ---------------------------------------------------------------------------
# Synthetic store
# ---------------------------------------------------------------------------

_DEV_END = C.DEVELOPMENT_END


def _canonical(obj: object) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _write_parquet(root: Path, relative: str, frame: pd.DataFrame) -> str:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path, engine="pyarrow", index=False, compression="zstd")
    return _sha256_file(path)


def _write_manifest(root: Path, relative: str, field: str, files: dict) -> str:
    content = {"files_sha256": files}
    content[field] = hashlib.sha256(_canonical(files).encode("utf-8")).hexdigest()
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((_canonical(content) + "\n").encode("utf-8"))
    return content[field]


def _empty_stage(name: str) -> R.StageBinding:
    return R.StageBinding(
        name,
        f"{name}-dummy",
        f"dummy/{name}.json",
        f"{name}_hash",
        "0" * 64,
        "0" * 64,
        (R.FileBinding(f"dummy/{name}.parquet", "0" * 64),),
    )


class _Store:
    def __init__(
        self,
        tmp_path: Path,
        *,
        sessions: list[date],
        identities: list[tuple[str, str, date, date]],
        ca_events: list[dict[str, Any]],
        daily_by_key: dict[str, dict[date, float]],
        flags: list[dict[str, Any]],
        daily_counts: dict[tuple[str, date], int] | None = None,
    ) -> None:
        self.root = tmp_path / "root"
        self.root.mkdir(parents=True, exist_ok=True)
        self.sessions = sessions
        self.identities = identities
        self.ca_events = ca_events
        self.daily_by_key = daily_by_key
        self.flags = flags
        self.daily_counts = daily_counts
        self.source_bindings: S.SourceBindings | None = None

    # -- helpers ---------------------------------------------------------
    def _selected_counts(self) -> dict[tuple[str, date], int]:
        # D4 reuses tickers across keys and a ticker's event may fall inside
        # several keys' intervals, so count it for every containing key (the
        # D7a ``ca_event_count`` semantics).
        counts: dict[tuple[str, date], int] = {}
        for event in self.ca_events:
            effective = event["effective_date"]
            if event.get("is_superseded", False) or event["knowledge_date"] != effective:
                continue
            for security_key, ticker, start, end in self.identities:
                if ticker != event["stock_id"]:
                    continue
                if not (start <= effective <= min(end, _DEV_END)):
                    continue
                counts[(security_key, effective)] = (
                    counts.get((security_key, effective), 0) + 1
                )
        return counts

    # -- build -----------------------------------------------------------
    def build(self) -> None:
        root = self.root
        d3_id = "n-test"
        d4_id = "i-test"
        d7a_id = "r-test"

        observations = pd.DataFrame(
            {
                "ticker": pd.Series([], dtype="string"),
                "date": pd.Series([], dtype="datetime64[ns]"),
                "close": pd.Series([], dtype="float64"),
                "raw_row_index": pd.Series([], dtype="int64"),
            }
        )
        ca = pd.DataFrame(
            {
                "stock_id": pd.Series(
                    [event["stock_id"] for event in self.ca_events], dtype="string"
                ),
                "effective_date": pd.Series(
                    [event["effective_date"] for event in self.ca_events],
                    dtype="datetime64[ns]",
                ),
                "action_type": pd.Series(
                    [event["action_type"] for event in self.ca_events], dtype="string"
                ),
                "knowledge_date": pd.Series(
                    [event["knowledge_date"] for event in self.ca_events],
                    dtype="datetime64[ns]",
                ),
                "adjustment_factor": pd.Series(
                    [event["adjustment_factor"] for event in self.ca_events],
                    dtype="float64",
                ),
                "is_superseded": pd.Series(
                    [event.get("is_superseded", False) for event in self.ca_events],
                    dtype=bool,
                ),
            }
        )
        identity = pd.DataFrame(
            {
                "security_key": pd.Series(
                    [row[0] for row in self.identities], dtype="string"
                ),
                "ticker": pd.Series([row[1] for row in self.identities], dtype="string"),
                "listed_start": pd.Series(
                    [row[2] for row in self.identities], dtype="datetime64[ns]"
                ),
                "listed_end": pd.Series(
                    [row[3] for row in self.identities], dtype="datetime64[ns]"
                ),
            }
        )

        d3_obs = f"normalized/{d3_id}/observations.parquet"
        d3_ca = f"normalized/{d3_id}/ca_events.parquet"
        d4_file = f"derived/identity/{d4_id}/identity_assignment.parquet"
        sha_obs = _write_parquet(root, d3_obs, observations)
        sha_ca = _write_parquet(root, d3_ca, ca)
        sha_ident = _write_parquet(root, d4_file, identity)
        d3_hash = _write_manifest(
            root, f"normalized/{d3_id}/build.json", "normalized_hash",
            {d3_obs: sha_obs, d3_ca: sha_ca},
        )
        d3_msha = _sha256_file(root / f"normalized/{d3_id}/build.json")
        d4_hash = _write_manifest(
            root, f"derived/identity/{d4_id}/identity.json", "identity_hash",
            {d4_file: sha_ident},
        )
        d4_msha = _sha256_file(root / f"derived/identity/{d4_id}/identity.json")

        ca_bindings = R.Bindings(
            d3=R.StageBinding(
                "D3", d3_id, f"normalized/{d3_id}/build.json", "normalized_hash",
                d3_hash, d3_msha,
                (R.FileBinding(d3_obs, sha_obs), R.FileBinding(d3_ca, sha_ca)),
            ),
            d4=R.StageBinding(
                "D4", d4_id, f"derived/identity/{d4_id}/identity.json",
                "identity_hash", d4_hash, d4_msha,
                (R.FileBinding(d4_file, sha_ident),),
            ),
            d5=_empty_stage("D5"),
            d6=_empty_stage("D6"),
            documents=(),
        )

        # -- D7A artifact -------------------------------------------------
        rows: list[dict[str, Any]] = []
        counts = self.daily_counts if self.daily_counts is not None else self._selected_counts()
        for key, series in sorted(self.daily_by_key.items()):
            for session, value in sorted(series.items()):
                rows.append(
                    {
                        "security_key": key,
                        "date": session,
                        "session_status": "VALID",
                        "daily_total_return": value,
                        "ca_event_count": counts.get((key, session), 0),
                    }
                )
        daily = pd.DataFrame(
            {
                "security_key": pd.Series(
                    [row["security_key"] for row in rows], dtype="string"
                ),
                "date": pd.Series(
                    [row["date"] for row in rows], dtype="datetime64[ns]"
                ),
                "session_status": pd.Series(
                    [row["session_status"] for row in rows], dtype="string"
                ),
                "daily_total_return": pd.Series(
                    [row["daily_total_return"] for row in rows], dtype="float64"
                ),
                "ca_event_count": pd.Series(
                    [row["ca_event_count"] for row in rows], dtype="int64"
                ),
            }
        )
        members = pd.DataFrame(
            {
                "formation_date": pd.Series([], dtype="datetime64[ns]"),
                "security_key": pd.Series([], dtype="string"),
            }
        )
        horizons = pd.DataFrame(
            {
                "formation_date": pd.Series([], dtype="datetime64[ns]"),
                "endpoint": pd.Series([], dtype="datetime64[ns]"),
                "crosses_reserve": pd.Series([], dtype=bool),
            }
        )
        labels = pd.DataFrame(
            {
                "formation_date": pd.Series([], dtype="datetime64[ns]"),
                "security_key": pd.Series([], dtype="string"),
                "observed_segment_end": pd.Series([], dtype="datetime64[ns]"),
            }
        )
        d7a_files: dict[str, str] = {}
        for name, frame in (
            ("members.parquet", members),
            ("horizons.parquet", horizons),
            ("labels.parquet", labels),
            ("daily_returns.parquet", daily),
        ):
            relative = f"derived/returns/{d7a_id}/{name}"
            d7a_files[relative] = _write_parquet(root, relative, frame)
        returns_hash = hashlib.sha256(
            _canonical(d7a_files).encode("utf-8")
        ).hexdigest()
        manifest = {"returns_id": d7a_id, "returns_hash": returns_hash,
                    "files_sha256": d7a_files}
        manifest_path = root / f"derived/returns/{d7a_id}/returns.json"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_bytes((_canonical(manifest) + "\n").encode("utf-8"))
        manifest_sha = _sha256_file(manifest_path)
        d7b_bindings = B.Bindings(
            d7a=B.D7aBinding(
                artifact_id=d7a_id,
                returns_hash=returns_hash,
                manifest_relative_path=f"derived/returns/{d7a_id}/returns.json",
                manifest_sha256=manifest_sha,
                files=tuple(
                    B.FileBinding(relative, digest)
                    for relative, digest in sorted(d7a_files.items())
                ),
            ),
            code_sha256=(),
            documents=(),
        )
        self.source_bindings = S.SourceBindings(ca=ca_bindings, d7a=d7b_bindings)

    # -- overlays / trusted inputs --------------------------------------
    def overlay(self) -> Q.Overlay:
        frame = pd.DataFrame(
            {
                "security_key": pd.Series(
                    [flag["security_key"] for flag in self.flags], dtype="string"
                ),
                "flag_session": pd.Series(
                    [flag["flag_session"] for flag in self.flags],
                    dtype="datetime64[ns]",
                ),
                "prev_session": pd.Series(
                    [flag["prev_session"] for flag in self.flags],
                    dtype="datetime64[ns]",
                ),
                "next_session": pd.Series(
                    [flag["next_session"] for flag in self.flags],
                    dtype="datetime64[ns]",
                ),
                "next_beyond_development": pd.Series(
                    [flag.get("next_beyond_development", False) for flag in self.flags],
                    dtype=bool,
                ),
            }
        )
        return Q.Overlay(
            flags=frame,
            primitive_states=pd.DataFrame(),
            input_states=pd.DataFrame(),
            label_states=pd.DataFrame(),
            liquidity_exposure=pd.DataFrame(),
            identity={},
        )

    def trusted(self) -> dict[str, TrustedInput]:
        index = pd.DatetimeIndex(
            pd.to_datetime(sorted(self.sessions)), name=None
        )
        columns = sorted(self.daily_by_key)
        frame = pd.DataFrame(np.nan, index=index, columns=columns, dtype="float64")
        position = {session: i for i, session in enumerate(self.sessions)}
        for key, series in self.daily_by_key.items():
            for session, value in series.items():
                index_at = position[session]
                if index_at + 1 < len(self.sessions):
                    frame.loc[pd.Timestamp(self.sessions[index_at + 1]), key] = value
        capability = DataCapability(
            semantic_id="daily_total_return",
            frequency=Frequency.DAILY,
            observation_period=ObservationPeriod.PERIOD,
            units=Unit.FRACTION,
            history=len(self.sessions),
            has_knowledge_date=True,
            has_positive_vintage_identity=False,
            revision_policies=frozenset({RevisionPolicy.POINT_IN_TIME}),
        )
        return {
            "daily_total_return": TrustedInput(
                values=frame,
                capability=capability,
                evidence_class=EvidenceClass.LIVE_RECORDED,
            )
        }


def _consecutive(start: date, n: int, step: int = 1) -> list[date]:
    return [start + timedelta(days=step * index) for index in range(n)]


def _store(tmp_path: Path, **overrides: Any) -> _Store:
    base = dict(
        sessions=_consecutive(date(2023, 5, 1), 75),
        identities=[("K1", "AAA", date(2020, 1, 1), date(2099, 1, 1))],
        ca_events=[],
        daily_by_key={},
        flags=[],
    )
    base.update(overrides)
    store = _Store(tmp_path, **base)
    store.build()
    return store


def _load(store: _Store) -> C.SourceSet:
    assert store.source_bindings is not None
    return S.load_source_set(
        store.root,
        overlay=store.overlay(),
        d7a_bindings=store.source_bindings,
        ti_inputs=store.trusted(),
        sessions=store.sessions,
    )


def _flag(session: date, prev: date, nxt: date, key: str = "K1", beyond: bool = False):
    return {
        "security_key": key,
        "flag_session": session,
        "prev_session": prev,
        "next_session": nxt,
        "next_beyond_development": beyond,
    }


# ---------------------------------------------------------------------------
# Basic loading
# ---------------------------------------------------------------------------


def test_load_source_set_basic(tmp_path: Path) -> None:
    sessions = _consecutive(date(2023, 5, 1), 30)
    flag_session = sessions[10]
    daily = {"K1": {session: 0.02 for session in sessions}}
    store = _store(
        tmp_path,
        sessions=sessions,
        daily_by_key=daily,
        flags=[_flag(flag_session, sessions[9], sessions[11])],
    )
    source = _load(store)
    assert list(source.transitions_by_key) == ["K1"]
    transition = source.transitions_by_key["K1"][0]
    assert transition.s == flag_session
    assert transition.prev_s == sessions[9]
    assert transition.next_s == sessions[11]
    assert transition.m_obs == pytest.approx(1.02)
    assert transition.rel.lo == pytest.approx(min(0.5, 1.02) / 1.02)
    assert transition.rel.hi == pytest.approx(max(1.5, 1.02) / 1.02)
    assert source.candidates_by_key == {}
    assert source.unknown_by_key == {}


def test_trusted_input_mapping_and_object_are_equivalent(tmp_path: Path) -> None:
    sessions = _consecutive(date(2023, 5, 1), 20)
    daily = {"K1": {session: 0.05 for session in sessions}}
    store = _store(
        tmp_path,
        sessions=sessions,
        daily_by_key=daily,
        flags=[_flag(sessions[8], sessions[7], sessions[9])],
    )
    assert store.source_bindings is not None
    mapping = store.trusted()["daily_total_return"]
    from_mapping = S.load_source_set(
        store.root, overlay=store.overlay(), d7a_bindings=store.source_bindings,
        ti_inputs=store.trusted(), sessions=store.sessions,
    )
    from_object = S.load_source_set(
        store.root, overlay=store.overlay(), d7a_bindings=store.source_bindings,
        ti_inputs=mapping, sessions=store.sessions,
    )
    assert from_mapping == from_object


def test_determinism(tmp_path: Path) -> None:
    sessions = _consecutive(date(2023, 5, 1), 20)
    store = _store(
        tmp_path,
        sessions=sessions,
        daily_by_key={"K1": {session: 0.03 for session in sessions}},
        flags=[_flag(sessions[8], sessions[7], sessions[9])],
    )
    assert _load(store) == _load(store)


# ---------------------------------------------------------------------------
# MISDATED candidates
# ---------------------------------------------------------------------------


def test_candidates_plus_minus_five_boundary(tmp_path: Path) -> None:
    sessions = _consecutive(date(2023, 5, 1), 20)
    flag_session = sessions[10]
    events = [
        {
            "stock_id": "AAA",
            "effective_date": sessions[4],  # trusted distance 6 -> excluded
            "action_type": "split",
            "knowledge_date": sessions[4],
            "adjustment_factor": 1.10,
        },
        {
            "stock_id": "AAA",
            "effective_date": sessions[5],  # trusted distance 5 -> included
            "action_type": "split",
            "knowledge_date": sessions[5],
            "adjustment_factor": 1.20,
        },
        {
            "stock_id": "AAA",
            "effective_date": sessions[15],  # trusted distance 5 -> included
            "action_type": "dividend",
            "knowledge_date": sessions[15],
            "adjustment_factor": 0.90,
        },
        {
            "stock_id": "AAA",
            "effective_date": sessions[16],  # trusted distance 6 -> excluded
            "action_type": "dividend",
            "knowledge_date": sessions[16],
            "adjustment_factor": 0.80,
        },
    ]
    store = _store(
        tmp_path,
        sessions=sessions,
        ca_events=events,
        daily_by_key={"K1": {session: 0.01 for session in sessions}},
        flags=[_flag(flag_session, sessions[9], sessions[11])],
    )
    source = _load(store)
    candidates = source.candidates_by_key["K1"]
    assert [(c.d, c.phi_d) for c in candidates] == [
        (sessions[5], pytest.approx(1.20)),
        (sessions[15], pytest.approx(0.90)),
    ]
    assert all(c.s == flag_session for c in candidates)


def test_window_is_trusted_sessions_not_calendar_days(tmp_path: Path) -> None:
    # Sessions are three calendar days apart, so trusted distance 2 == 6
    # calendar days and trusted distance 5 == 15 calendar days.  All are inside
    # the frozen trusted-session window.
    sessions = _consecutive(date(2023, 5, 1), 20, step=3)
    flag_session = sessions[10]
    events = [
        {
            "stock_id": "AAA",
            "effective_date": sessions[8],  # trusted 2, calendar 6
            "action_type": "split",
            "knowledge_date": sessions[8],
            "adjustment_factor": 1.10,
        },
        {
            "stock_id": "AAA",
            "effective_date": sessions[5],  # trusted 5, calendar 15
            "action_type": "dividend",
            "knowledge_date": sessions[5],
            "adjustment_factor": 0.95,
        },
    ]
    store = _store(
        tmp_path,
        sessions=sessions,
        ca_events=events,
        daily_by_key={"K1": {session: 0.01 for session in sessions}},
        flags=[_flag(flag_session, sessions[9], sessions[11])],
    )
    source = _load(store)
    assert {c.d for c in source.candidates_by_key["K1"]} == {
        sessions[8],
        sessions[5],
    }


def test_no_recorded_event_is_never_a_candidate(tmp_path: Path) -> None:
    sessions = _consecutive(date(2023, 5, 1), 20)
    flag_session = sessions[10]
    events = [
        {
            "stock_id": "AAA",
            "effective_date": sessions[17],  # trusted distance 7 -> outside
            "action_type": "split",
            "knowledge_date": sessions[17],
            "adjustment_factor": 1.20,
        }
    ]
    store = _store(
        tmp_path,
        sessions=sessions,
        ca_events=events,
        daily_by_key={"K1": {session: 0.01 for session in sessions}},
        flags=[_flag(flag_session, sessions[9], sessions[11])],
    )
    source = _load(store)
    # Every session in the +-5 window has no recorded event, so the candidate
    # set is empty (V2-M04: "every +-5 session" must not be a candidate).
    assert source.candidates_by_key.get("K1", ()) == ()


def test_action_type_filter(tmp_path: Path) -> None:
    sessions = _consecutive(date(2023, 5, 1), 20)
    flag_session = sessions[10]
    events = [
        {
            "stock_id": "AAA",
            "effective_date": sessions[8],
            "action_type": "rights",
            "knowledge_date": sessions[8],
            "adjustment_factor": 1.05,
        },
        {
            "stock_id": "AAA",
            "effective_date": sessions[9],
            "action_type": "split",
            "knowledge_date": sessions[9],
            "adjustment_factor": 1.10,
        },
    ]
    store = _store(
        tmp_path,
        sessions=sessions,
        ca_events=events,
        daily_by_key={"K1": {session: 0.01 for session in sessions}},
        flags=[_flag(flag_session, sessions[9], sessions[11])],
    )
    source = _load(store)
    assert {c.d for c in source.candidates_by_key["K1"]} == {sessions[9]}


def test_d4_interval_filter(tmp_path: Path) -> None:
    sessions = _consecutive(date(2023, 5, 1), 20)
    flag_session = sessions[10]
    listed_start = sessions[9]
    events = [
        {
            "stock_id": "AAA",
            "effective_date": sessions[8],  # before listed_start -> excluded
            "action_type": "split",
            "knowledge_date": sessions[8],
            "adjustment_factor": 1.10,
        },
        {
            "stock_id": "AAA",
            "effective_date": sessions[9],
            "action_type": "split",
            "knowledge_date": sessions[9],
            "adjustment_factor": 1.20,
        },
    ]
    store = _store(
        tmp_path,
        sessions=sessions,
        identities=[("K1", "AAA", listed_start, date(2099, 1, 1))],
        ca_events=events,
        daily_by_key={"K1": {session: 0.01 for session in sessions}},
        flags=[_flag(flag_session, sessions[9], sessions[11])],
    )
    source = _load(store)
    assert {c.d for c in source.candidates_by_key["K1"]} == {sessions[9]}


def test_same_day_events_combined_factor(tmp_path: Path) -> None:
    sessions = _consecutive(date(2023, 5, 1), 20)
    flag_session = sessions[10]
    events = [
        {
            "stock_id": "AAA",
            "effective_date": sessions[8],
            "action_type": "split",
            "knowledge_date": sessions[8],
            "adjustment_factor": 1.20,
        },
        {
            "stock_id": "AAA",
            "effective_date": sessions[8],
            "action_type": "dividend",
            "knowledge_date": sessions[8],
            "adjustment_factor": 0.90,
        },
    ]
    store = _store(
        tmp_path,
        sessions=sessions,
        ca_events=events,
        daily_by_key={"K1": {session: 0.01 for session in sessions}},
        flags=[_flag(flag_session, sessions[9], sessions[11])],
    )
    source = _load(store)
    candidates = source.candidates_by_key["K1"]
    assert len(candidates) == 1
    # B-M4: the combined phi_d is the product, not just the first event factor.
    assert candidates[0].phi_d == pytest.approx(1.20 * 0.90)


def test_candidates_present(tmp_path: Path) -> None:
    sessions = _consecutive(date(2023, 5, 1), 20)
    flag_session = sessions[10]
    events = [
        {
            "stock_id": "AAA",
            "effective_date": sessions[8],
            "action_type": "split",
            "knowledge_date": sessions[8],
            "adjustment_factor": 1.30,
        }
    ]
    store = _store(
        tmp_path,
        sessions=sessions,
        ca_events=events,
        daily_by_key={"K1": {session: 0.01 for session in sessions}},
        flags=[_flag(flag_session, sessions[9], sessions[11])],
    )
    source = _load(store)
    # V2-M03: candidates must not be dropped.
    assert len(source.candidates_by_key["K1"]) == 1


# ---------------------------------------------------------------------------
# Firewall / reserve
# ---------------------------------------------------------------------------


def test_reserve_firewall_marks_unknown(tmp_path: Path) -> None:
    sessions = _consecutive(date(2023, 5, 1), 75)
    dev_position = sessions.index(_DEV_END)
    near_end = sessions[dev_position - 2]
    far_from_end = sessions[dev_position - 20]
    store = _store(
        tmp_path,
        sessions=sessions,
        daily_by_key={"K1": {session: 0.01 for session in sessions}},
        flags=[
            _flag(near_end, sessions[dev_position - 3], sessions[dev_position - 1]),
            _flag(far_from_end, sessions[dev_position - 21], sessions[dev_position - 19]),
        ],
    )
    source = _load(store)
    # The near-end flag's +5 window crosses DEVELOPMENT_END -> UNKNOWN_RESERVE,
    # even though its D7c m_s is finite; the far flag is resolved.
    unknown = source.unknown_by_key["K1"]
    assert [(flag.s, flag.state) for flag in unknown] == [
        (near_end, C.SourceState.UNKNOWN_RESERVE)
    ]
    assert far_from_end in {t.s for t in source.transitions_by_key["K1"]}


def test_next_beyond_development_marks_unknown(tmp_path: Path) -> None:
    sessions = _consecutive(date(2023, 5, 1), 30)
    flag_session = sessions[10]
    store = _store(
        tmp_path,
        sessions=sessions,
        daily_by_key={"K1": {session: 0.01 for session in sessions}},
        flags=[_flag(flag_session, sessions[9], sessions[11], beyond=True)],
    )
    source = _load(store)
    unknown = source.unknown_by_key["K1"]
    assert [(flag.s, flag.state) for flag in unknown] == [
        (flag_session, C.SourceState.UNKNOWN_RESERVE)
    ]
    assert source.transitions_by_key.get("K1", ()) == ()


def test_post_development_row_raises(tmp_path: Path) -> None:
    sessions = _consecutive(date(2023, 5, 1), 75)
    dev_position = sessions.index(_DEV_END)
    after_end = sessions[dev_position + 2]
    events = [
        {
            "stock_id": "AAA",
            "effective_date": after_end,
            "action_type": "split",
            "knowledge_date": after_end,
            "adjustment_factor": 1.10,
        }
    ]
    store = _store(
        tmp_path,
        sessions=sessions,
        ca_events=events,
        daily_by_key={"K1": {session: 0.01 for session in sessions}},
        flags=[_flag(sessions[10], sessions[9], sessions[11])],
    )
    with pytest.raises(C.PQv2InputError):
        _load(store)


def test_missing_m_s_is_unknown_source(tmp_path: Path) -> None:
    sessions = _consecutive(date(2023, 5, 1), 20)
    store = _store(
        tmp_path,
        sessions=sessions,
        ca_events=[],
        daily_by_key={"K1": {}},  # no D7c return at all
        flags=[_flag(sessions[10], sessions[9], sessions[11])],
    )
    source = _load(store)
    assert source.transitions_by_key.get("K1", ()) == ()
    unknown = source.unknown_by_key["K1"]
    assert len(unknown) == 1
    assert unknown[0].state == C.SourceState.UNKNOWN_SOURCE
    assert unknown[0].s == sessions[10]
    assert unknown[0].prev_s == sessions[9]
    assert not hasattr(unknown[0], "m_obs")


# ---------------------------------------------------------------------------
# v2.1 source classification (M§8A.1)
# ---------------------------------------------------------------------------


def _states(source: C.SourceSet, key: str) -> dict[date, str]:
    return {flag.s: flag.state for flag in source.unknown_by_key.get(key, ())}


def test_finite_m_s_is_resolved(tmp_path: Path) -> None:
    sessions = _consecutive(date(2023, 5, 1), 20)
    flag_session = sessions[10]
    store = _store(
        tmp_path,
        sessions=sessions,
        daily_by_key={"K1": {flag_session: 0.05}},
        flags=[_flag(flag_session, sessions[9], sessions[11])],
    )
    source = _load(store)
    assert source.unknown_by_key == {}
    assert list(source.transitions_by_key) == ["K1"]
    assert source.transitions_by_key["K1"][0].m_obs == pytest.approx(1.05)


def test_absent_row_inside_development_is_unknown_source(tmp_path: Path) -> None:
    sessions = _consecutive(date(2023, 5, 1), 20)
    flag_session = sessions[10]
    # A valid return on another date for the same key must not substitute.
    store = _store(
        tmp_path,
        sessions=sessions,
        daily_by_key={"K1": {sessions[2]: 0.05}},
        flags=[_flag(flag_session, sessions[9], sessions[11])],
    )
    source = _load(store)
    assert _states(source, "K1") == {
        flag_session: C.SourceState.UNKNOWN_SOURCE
    }
    assert source.transitions_by_key.get("K1", ()) == ()


@pytest.mark.parametrize(
    "bad",
    [float("nan"), float("inf"), float("-inf"), -1.0, -1.5],
)
def test_unusable_m_s_is_unknown_source(tmp_path: Path, bad: float) -> None:
    sessions = _consecutive(date(2023, 5, 1), 20)
    flag_session = sessions[10]
    store = _store(
        tmp_path,
        sessions=sessions,
        daily_by_key={"K1": {flag_session: bad}},
        flags=[_flag(flag_session, sessions[9], sessions[11])],
    )
    source = _load(store)
    assert _states(source, "K1") == {
        flag_session: C.SourceState.UNKNOWN_SOURCE
    }


def test_never_in_d7c_key_is_unknown_source_without_abort(tmp_path: Path) -> None:
    sessions = _consecutive(date(2023, 5, 1), 20)
    flag_session = sessions[10]
    store = _store(
        tmp_path,
        sessions=sessions,
        # K2 has no D7c column at all; K1 is resolved.
        daily_by_key={"K1": {session: 0.01 for session in sessions}},
        flags=[
            _flag(flag_session, sessions[9], sessions[11], key="K1"),
            _flag(flag_session, sessions[9], sessions[11], key="K2"),
        ],
    )
    source = _load(store)
    assert _states(source, "K2") == {
        flag_session: C.SourceState.UNKNOWN_SOURCE
    }
    assert list(source.transitions_by_key) == ["K1"]
    assert source.transitions_by_key["K1"][0].m_obs == pytest.approx(1.01)


def test_d7a_valid_return_does_not_substitute(tmp_path: Path) -> None:
    sessions = _consecutive(date(2023, 5, 1), 20)
    flag_session = sessions[10]
    # A D7a-valid return at the exact row next(s) exists, but the authorised
    # D7c row there is absent -> UNKNOWN_SOURCE.
    store = _store(
        tmp_path,
        sessions=sessions,
        daily_by_key={"K1": {sessions[11]: 0.5}},
        flags=[_flag(flag_session, sessions[9], sessions[11])],
    )
    source = _load(store)
    assert _states(source, "K1") == {
        flag_session: C.SourceState.UNKNOWN_SOURCE
    }
    assert source.transitions_by_key.get("K1", ()) == ()


def test_unresolved_flags_appear_once_and_sorted(tmp_path: Path) -> None:
    sessions = _consecutive(date(2023, 5, 1), 20)
    store = _store(
        tmp_path,
        sessions=sessions,
        daily_by_key={"K1": {}},
        flags=[
            _flag(sessions[12], sessions[11], sessions[13]),
            _flag(sessions[6], sessions[5], sessions[7]),
        ],
    )
    source = _load(store)
    unknown = source.unknown_by_key["K1"]
    assert [flag.s for flag in unknown] == [sessions[6], sessions[12]]
    assert len(unknown) == len(set(unknown)) == 2
    assert all(flag.state == C.SourceState.UNKNOWN_SOURCE for flag in unknown)


def test_unknown_classification_is_row_order_invariant(tmp_path: Path) -> None:
    sessions = _consecutive(date(2023, 5, 1), 20)
    daily = {"K1": {sessions[5]: 0.02}}
    flags = [
        _flag(sessions[10], sessions[9], sessions[11]),
        _flag(sessions[6], sessions[5], sessions[7]),
        _flag(sessions[2], sessions[1], sessions[3]),
    ]
    forward = _store(tmp_path / "forward", sessions=sessions, daily_by_key=daily, flags=flags)
    reverse = _store(
        tmp_path / "reverse",
        sessions=sessions,
        daily_by_key=daily,
        flags=list(reversed(flags)),
    )
    assert _load(forward) == _load(reverse)


def test_reserve_classification_reads_no_reserve_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sessions = _consecutive(date(2023, 5, 1), 75)
    dev_position = sessions.index(_DEV_END)
    near_end = sessions[dev_position - 2]
    store = _store(
        tmp_path,
        sessions=sessions,
        daily_by_key={"K1": {session: 0.01 for session in sessions}},
        flags=[_flag(near_end, sessions[dev_position - 3], sessions[dev_position - 1])],
    )
    opened: list[str] = []
    real_open = builtins.open
    real_read_parquet = pd.read_parquet

    def _open(file: Any, *args: Any, **kwargs: Any):
        opened.append(str(file))
        return real_open(file, *args, **kwargs)

    def _read_parquet(path: Any, *args: Any, **kwargs: Any):
        opened.append(str(path))
        return real_read_parquet(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", _open)
    monkeypatch.setattr(pd, "read_parquet", _read_parquet)
    source = _load(store)
    assert _states(source, "K1") == {
        near_end: C.SourceState.UNKNOWN_RESERVE
    }
    forbidden = ("reserve_sealed", "raw/", "dsc_evidence", "/raw", "knowledge_pit")
    assert [path for path in opened if any(t in path for t in forbidden)] == []


def test_ca_event_count_mismatch_raises(tmp_path: Path) -> None:
    sessions = _consecutive(date(2023, 5, 1), 20)
    flag_session = sessions[10]
    events = [
        {
            "stock_id": "AAA",
            "effective_date": sessions[8],
            "action_type": "split",
            "knowledge_date": sessions[8],
            "adjustment_factor": 1.20,
        }
    ]
    store = _store(
        tmp_path,
        sessions=sessions,
        ca_events=events,
        daily_by_key={"K1": {session: 0.01 for session in sessions}},
        flags=[_flag(flag_session, sessions[9], sessions[11])],
        daily_counts={("K1", sessions[8]): 3},  # mismatch
    )
    with pytest.raises(C.PQv2InputError):
        _load(store)


# ---------------------------------------------------------------------------
# Binding verification
# ---------------------------------------------------------------------------


def test_binding_hash_mismatch_raises(tmp_path: Path) -> None:
    sessions = _consecutive(date(2023, 5, 1), 20)
    store = _store(
        tmp_path,
        sessions=sessions,
        ca_events=[],
        daily_by_key={"K1": {session: 0.01 for session in sessions}},
        flags=[_flag(sessions[10], sessions[9], sessions[11])],
    )
    assert store.source_bindings is not None
    ca_path = store.root / store.source_bindings.ca.d3.files[1].relative_path
    ca_path.write_bytes(ca_path.read_bytes() + b"tampered")
    with pytest.raises(C.PQv2BindingError):
        _load(store)


# ---------------------------------------------------------------------------
# Ticker reuse / D4 interval assignment (PQ2-B-R1)
# ---------------------------------------------------------------------------
# D4 reuses tickers across distinct security keys; a ticker's event belongs to
# every key whose clipped D4 interval contains its effective date (the D7a
# ``_member_ca_events`` rule).  These tests are synthetic-only and assert that
# the loader never collapses a ticker onto a single key.


def _ca_event(
    stock_id: str,
    effective: date,
    factor: float,
    action: str = "split",
    knowledge: date | None = None,
    superseded: bool = False,
) -> dict[str, Any]:
    return {
        "stock_id": stock_id,
        "effective_date": effective,
        "action_type": action,
        "knowledge_date": effective if knowledge is None else knowledge,
        "adjustment_factor": factor,
        "is_superseded": superseded,
    }


def _identity_frame(identities: list[tuple[str, str, date, date]]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "security_key": pd.Series([row[0] for row in identities], dtype="string"),
            "ticker": pd.Series([row[1] for row in identities], dtype="string"),
            "listed_start": pd.Series(
                [row[2] for row in identities], dtype="datetime64[ns]"
            ),
            "listed_end": pd.Series(
                [row[3] for row in identities], dtype="datetime64[ns]"
            ),
        }
    )


def _ca_frame(events: list[dict[str, Any]]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "stock_id": pd.Series([e["stock_id"] for e in events], dtype="string"),
            "effective_date": pd.Series(
                [e["effective_date"] for e in events], dtype="datetime64[ns]"
            ),
            "action_type": pd.Series(
                [e["action_type"] for e in events], dtype="string"
            ),
            "knowledge_date": pd.Series(
                [e["knowledge_date"] for e in events], dtype="datetime64[ns]"
            ),
            "adjustment_factor": pd.Series(
                [e["adjustment_factor"] for e in events], dtype="float64"
            ),
            "is_superseded": pd.Series(
                [e.get("is_superseded", False) for e in events], dtype=bool
            ),
        }
    )


def _direct_events(
    identities: list[tuple[str, str, date, date]],
    events: list[dict[str, Any]],
    sessions: list[date],
) -> dict[str, dict[date, tuple[tuple[str, bool, float], ...]]]:
    index = S._identity_index(_identity_frame(identities))
    return S._event_index(_ca_frame(events), sessions, index)


def test_unique_ticker_unchanged() -> None:
    sessions = _consecutive(date(2023, 5, 1), 12)
    result = _direct_events(
        [("K1", "AAA", sessions[0], sessions[11])],
        [_ca_event("AAA", sessions[3], 1.10)],
        sessions,
    )
    assert set(result) == {"K1"}
    assert set(result["K1"]) == {sessions[3]}


def test_event_before_listed_start_not_assigned() -> None:
    sessions = _consecutive(date(2023, 5, 1), 12)
    result = _direct_events(
        [("K1", "AAA", sessions[5], sessions[11])],
        [_ca_event("AAA", sessions[2], 1.10)],
        sessions,
    )
    assert "K1" not in result


def test_event_after_listed_end_not_assigned() -> None:
    sessions = _consecutive(date(2023, 5, 1), 12)
    result = _direct_events(
        [("K1", "AAA", sessions[0], sessions[5])],
        [_ca_event("AAA", sessions[8], 1.10)],
        sessions,
    )
    assert "K1" not in result


def test_interval_boundaries_are_inclusive() -> None:
    sessions = _consecutive(date(2023, 5, 1), 75)
    dev_end = _DEV_END
    result = _direct_events(
        [
            ("K1", "AAA", sessions[5], sessions[8]),
            ("K2", "BBB", sessions[5], date(2099, 1, 1)),
        ],
        [
            _ca_event("AAA", sessions[5], 1.10),  # listed_start inclusive
            _ca_event("AAA", sessions[8], 1.20),  # listed_end inclusive
            _ca_event("AAA", sessions[9], 1.30),  # past listed_end
            _ca_event("BBB", sessions[5], 1.40),  # listed_start inclusive
            _ca_event("BBB", dev_end, 1.50),  # clipped end == DEVELOPMENT_END
        ],
        sessions,
    )
    assert set(result["K1"]) == {sessions[5], sessions[8]}
    assert set(result["K2"]) == {sessions[5], dev_end}


def test_event_index_matches_d7a_member_selection() -> None:
    # Test-only import of the D7a semantic reference: the synthetic selection
    # must agree with returns._member_ca_events over reused-ticker intervals.
    sessions = _consecutive(date(2023, 5, 1), 20)
    identities = [
        ("K1", "AAA", sessions[0], sessions[9]),
        ("K2", "AAA", sessions[5], sessions[19]),
    ]
    events = [
        _ca_event("AAA", sessions[2], 1.10),
        _ca_event("AAA", sessions[7], 0.90, action="dividend"),
        _ca_event("AAA", sessions[12], 1.20),
    ]
    result = _direct_events(identities, events, sessions)
    by_ticker = R._index_ca_events(_ca_frame(events))
    for key, ticker, start, end in identities:
        expected = R._member_ca_events(
            by_ticker.get(ticker, []), start, min(end, _DEV_END)
        )
        expected_by_session: dict[date, list[tuple[str, float]]] = {}
        for event in expected:
            expected_by_session.setdefault(event.effective_date, []).append(
                (event.action_type, event.adjustment_factor)
            )
        actual = result.get(key, {})
        assert set(actual) == set(expected_by_session)
        for session, pairs in expected_by_session.items():
            actual_pairs = sorted(
                (action, factor) for action, _candidate, factor in actual[session]
            )
            assert actual_pairs == sorted(pairs)


def test_reused_ticker_disjoint_intervals(tmp_path: Path) -> None:
    sessions = _consecutive(date(2023, 5, 1), 20)
    store = _store(
        tmp_path,
        sessions=sessions,
        identities=[
            ("K1", "AAA", sessions[0], sessions[4]),
            ("K2", "AAA", sessions[10], sessions[19]),
        ],
        ca_events=[
            _ca_event("AAA", sessions[1], 1.10),
            _ca_event("AAA", sessions[11], 0.90),
        ],
        daily_by_key={
            "K1": {session: 0.01 for session in sessions},
            "K2": {session: 0.01 for session in sessions},
        },
        flags=[
            _flag(sessions[2], sessions[1], sessions[3], key="K1"),
            _flag(sessions[12], sessions[11], sessions[13], key="K2"),
        ],
    )
    source = _load(store)
    assert {c.d for c in source.candidates_by_key["K1"]} == {sessions[1]}
    assert {c.d for c in source.candidates_by_key["K2"]} == {sessions[11]}


def test_reused_ticker_overlapping_intervals(tmp_path: Path) -> None:
    # A shared event on an overlap must appear for BOTH containing keys
    # (kills a one-to-one ticker->key assumption, e.g. B-M6).
    sessions = _consecutive(date(2023, 5, 1), 20)
    flag_session = sessions[8]
    store = _store(
        tmp_path,
        sessions=sessions,
        identities=[
            ("K1", "AAA", sessions[0], sessions[9]),
            ("K2", "AAA", sessions[5], sessions[19]),
        ],
        ca_events=[_ca_event("AAA", sessions[7], 1.25)],
        daily_by_key={
            "K1": {session: 0.01 for session in sessions},
            "K2": {session: 0.01 for session in sessions},
        },
        flags=[
            _flag(flag_session, sessions[7], sessions[9], key="K1"),
            _flag(flag_session, sessions[7], sessions[9], key="K2"),
        ],
    )
    source = _load(store)
    for key in ("K1", "K2"):
        assert [(c.s, c.d, c.phi_d) for c in source.candidates_by_key[key]] == [
            (flag_session, sessions[7], pytest.approx(1.25))
        ]


def test_reused_ticker_gap_yields_no_candidate(tmp_path: Path) -> None:
    sessions = _consecutive(date(2023, 5, 1), 20)
    store = _store(
        tmp_path,
        sessions=sessions,
        identities=[
            ("K1", "AAA", sessions[0], sessions[4]),
            ("K2", "AAA", sessions[10], sessions[19]),
        ],
        ca_events=[_ca_event("AAA", sessions[7], 1.10)],  # between the intervals
        daily_by_key={
            "K1": {session: 0.01 for session in sessions},
            "K2": {session: 0.01 for session in sessions},
        },
        flags=[
            _flag(sessions[2], sessions[1], sessions[3], key="K1"),
            _flag(sessions[12], sessions[11], sessions[13], key="K2"),
        ],
    )
    source = _load(store)
    assert source.candidates_by_key.get("K1", ()) == ()
    assert source.candidates_by_key.get("K2", ()) == ()


def test_d4_row_order_is_permutation_invariant(tmp_path: Path) -> None:
    sessions = _consecutive(date(2023, 5, 1), 20)
    daily = {
        "K1": {session: 0.01 for session in sessions},
        "K2": {session: 0.01 for session in sessions},
    }
    flags = [
        _flag(sessions[8], sessions[7], sessions[9], key="K1"),
        _flag(sessions[8], sessions[7], sessions[9], key="K2"),
    ]
    event = _ca_event("AAA", sessions[7], 1.25)
    forward = _store(
        tmp_path / "forward",
        sessions=sessions,
        identities=[
            ("K1", "AAA", sessions[0], sessions[9]),
            ("K2", "AAA", sessions[5], sessions[19]),
        ],
        ca_events=[event],
        daily_by_key=daily,
        flags=flags,
    )
    reverse = _store(
        tmp_path / "reverse",
        sessions=sessions,
        identities=[
            ("K2", "AAA", sessions[5], sessions[19]),
            ("K1", "AAA", sessions[0], sessions[9]),
        ],
        ca_events=[event],
        daily_by_key=daily,
        flags=flags,
    )
    assert _load(forward) == _load(reverse)


def test_event_row_order_is_permutation_invariant(tmp_path: Path) -> None:
    sessions = _consecutive(date(2023, 5, 1), 20)
    daily = {
        "K1": {session: 0.01 for session in sessions},
        "K2": {session: 0.01 for session in sessions},
    }
    identities = [
        ("K1", "AAA", sessions[0], sessions[9]),
        ("K2", "AAA", sessions[5], sessions[19]),
    ]
    flags = [
        _flag(sessions[8], sessions[7], sessions[9], key="K1"),
        _flag(sessions[8], sessions[7], sessions[9], key="K2"),
    ]
    e1 = _ca_event("AAA", sessions[7], 1.25)
    e2 = _ca_event("AAA", sessions[7], 0.80, action="dividend")
    e3 = _ca_event("AAA", sessions[11], 1.10)
    forward = _store(
        tmp_path / "forward",
        sessions=sessions,
        identities=identities,
        ca_events=[e1, e2, e3],
        daily_by_key=daily,
        flags=flags,
    )
    reverse = _store(
        tmp_path / "reverse",
        sessions=sessions,
        identities=identities,
        ca_events=[e3, e2, e1],
        daily_by_key=daily,
        flags=flags,
    )
    assert _load(forward) == _load(reverse)


# ---------------------------------------------------------------------------
# File-open audit
# ---------------------------------------------------------------------------


def test_file_open_audit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sessions = _consecutive(date(2023, 5, 1), 20)
    flag_session = sessions[10]
    events = [
        {
            "stock_id": "AAA",
            "effective_date": sessions[8],
            "action_type": "split",
            "knowledge_date": sessions[8],
            "adjustment_factor": 1.20,
        }
    ]
    store = _store(
        tmp_path,
        sessions=sessions,
        ca_events=events,
        daily_by_key={"K1": {session: 0.01 for session in sessions}},
        flags=[_flag(flag_session, sessions[9], sessions[11])],
    )
    opened: list[str] = []
    real_open = builtins.open
    real_read_parquet = pd.read_parquet

    def _open(file: Any, *args: Any, **kwargs: Any):
        opened.append(str(file))
        return real_open(file, *args, **kwargs)

    def _read_parquet(path: Any, *args: Any, **kwargs: Any):
        opened.append(str(path))
        return real_read_parquet(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", _open)
    monkeypatch.setattr(pd, "read_parquet", _read_parquet)
    _load(store)

    forbidden = ("reserve_sealed", "raw/", "dsc_evidence", "/raw", "knowledge_pit")
    offender = [path for path in opened if any(token in path for token in forbidden)]
    assert offender == []
    assert opened  # the audit really observed reads


def test_only_owned_paths_below_root(tmp_path: Path) -> None:
    # The resolver refuses a path that is not in the bindings' allowlist.
    sessions = _consecutive(date(2023, 5, 1), 20)
    store = _store(tmp_path, sessions=sessions, daily_by_key={}, flags=[])
    assert store.source_bindings is not None
    resolver = R.Resolver(store.root, store.source_bindings.ca)
    with pytest.raises(R.ReserveFirewallError):
        resolver.resolve("reserve_sealed/secret.parquet")


# ---------------------------------------------------------------------------
# P-Q v2.4 reserve fold (PQ24-B)
# ---------------------------------------------------------------------------

#: The reserve fold used by the PQ24-B source tests: the reserve start and a
#: July data cutoff E.
_RESERVE_FOLD = C.Fold("reserve", C.RESERVE_START, date(2023, 7, 21))
#: A synthetic stitched session grid (dev rows before 2023-07-03, reserve rows
#: at/after it).
_RESERVE_SESSIONS = _consecutive(date(2023, 6, 19), 40)


def _load_fold(store: _Store, fold: C.Fold) -> C.SourceSet:
    assert store.source_bindings is not None
    return S.load_source_set(
        store.root,
        overlay=store.overlay(),
        d7a_bindings=store.source_bindings,
        ti_inputs=store.trusted(),
        sessions=store.sessions,
        fold=fold,
    )


def test_reserve_fold_next_within_fold_resolves(tmp_path: Path) -> None:
    # A reserve-dated flag whose next(s) is inside the fold's end E resolves;
    # a flag whose next(s) is beyond E is UNKNOWN_RESERVE.  The default fold's
    # E would be DEVELOPMENT_END, so B24-M1 kills the first half.
    sessions = _RESERVE_SESSIONS
    daily = {"K1": {s: (0.01 if s < C.RESERVE_START else 0.05) for s in sessions}}
    store = _store(
        tmp_path,
        sessions=sessions,
        daily_by_key=daily,
        flags=[
            _flag(date(2023, 7, 5), date(2023, 7, 4), date(2023, 7, 6)),
            _flag(date(2023, 7, 21), date(2023, 7, 20), date(2023, 7, 22)),
        ],
    )
    source = _load_fold(store, _RESERVE_FOLD)
    transitions = source.transitions_by_key["K1"]
    assert [t.s for t in transitions] == [date(2023, 7, 5)]
    assert transitions[0].m_obs == pytest.approx(1.05)
    assert [(flag.s, flag.state) for flag in source.unknown_by_key["K1"]] == [
        (date(2023, 7, 21), C.SourceState.UNKNOWN_RESERVE)
    ]


def test_reserve_fold_stitched_source_classification(tmp_path: Path) -> None:
    # The stitched D7c frame carries dev rows before RESERVE_START and reserve
    # rows at/after it, so each flag is classified by the source of its own
    # date.  B24-M2 (limiting the lookup to DEVELOPMENT_END) kills the reserve
    # half.
    sessions = _RESERVE_SESSIONS
    dev_flag = date(2023, 6, 27)  # next(s) 2023-06-28 -> development source
    reserve_flag = date(2023, 7, 5)  # next(s) 2023-07-06 -> reserve source
    daily = {"K1": {s: (0.01 if s < C.RESERVE_START else 0.05) for s in sessions}}
    store = _store(
        tmp_path,
        sessions=sessions,
        daily_by_key=daily,
        flags=[
            _flag(dev_flag, date(2023, 6, 26), date(2023, 6, 28)),
            _flag(reserve_flag, date(2023, 7, 4), date(2023, 7, 6)),
        ],
    )
    source = _load_fold(store, _RESERVE_FOLD)
    by_s = {t.s: t for t in source.transitions_by_key["K1"]}
    assert by_s[dev_flag].m_obs == pytest.approx(1.01)
    assert by_s[reserve_flag].m_obs == pytest.approx(1.05)
    assert source.unknown_by_key == {}


def test_reserve_fold_misdated_window_crossing_e(tmp_path: Path) -> None:
    # The flag's own next(s) is inside E, but its +5 trusted-session window
    # crosses E, so it is UNKNOWN_RESERVE (firewall §2.2).
    sessions = _RESERVE_SESSIONS
    near_end = date(2023, 7, 19)
    store = _store(
        tmp_path,
        sessions=sessions,
        daily_by_key={"K1": {s: 0.01 for s in sessions}},
        flags=[_flag(near_end, date(2023, 7, 18), date(2023, 7, 20))],
    )
    source = _load_fold(store, _RESERVE_FOLD)
    assert source.transitions_by_key.get("K1", ()) == ()
    assert [(flag.s, flag.state) for flag in source.unknown_by_key["K1"]] == [
        (near_end, C.SourceState.UNKNOWN_RESERVE)
    ]


def test_reserve_fold_rejects_bad_fold(tmp_path: Path) -> None:
    sessions = _consecutive(date(2023, 5, 1), 20)
    store = _store(tmp_path, sessions=sessions, daily_by_key={}, flags=[])
    with pytest.raises(C.PQv2InputError):
        _load_fold(store, "reserve")  # type: ignore[arg-type]


def test_no_reserve_open_under_default_fold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sessions = _RESERVE_SESSIONS
    store = _store(
        tmp_path,
        sessions=sessions,
        daily_by_key={"K1": {s: 0.01 for s in sessions}},
        flags=[_flag(date(2023, 6, 27), date(2023, 6, 26), date(2023, 6, 28))],
    )
    opened: list[str] = []
    real_open = builtins.open
    real_read_parquet = pd.read_parquet

    def _open(file: Any, *args: Any, **kwargs: Any):
        opened.append(str(file))
        return real_open(file, *args, **kwargs)

    def _read_parquet(path: Any, *args: Any, **kwargs: Any):
        opened.append(str(path))
        return real_read_parquet(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", _open)
    monkeypatch.setattr(pd, "read_parquet", _read_parquet)
    _load(store)

    forbidden = ("reserve_sealed", "raw/", "dsc_evidence", "/raw", "knowledge_pit")
    assert [path for path in opened if any(tok in path for tok in forbidden)] == []
    assert opened
