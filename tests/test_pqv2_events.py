"""PQ23-B — P-Q v2.3 event inputs tests (synthetic fixtures only).

Every test here is offline: an autouse socket guard and an eight-credential
scrub mirror the D7a/D7b/PQB suites, and no dataset artifact other than the
synthetic fixture root is opened.  Nothing under ``data_store/`` is read and no
network call is made.

The mutation fixture (``tests/fixtures/pqv2/events/mutations.json``) targets one
named test per mutation; the mapping is checked mechanically by
``test_mutation_fixture_contract``.
"""

from __future__ import annotations

import importlib.util
import dataclasses
import json
import socket
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from smart_beta.dataset_v1 import pqv2_core as C
from smart_beta.dataset_v1 import pqv2_events as E
from smart_beta.spec.engine import EvidenceClass
from smart_beta.spec.requirements import RevisionPolicy

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = REPO_ROOT / "tests" / "fixtures" / "pqv2" / "events" / "fixture.py"
MUTATIONS = REPO_ROOT / "tests" / "fixtures" / "pqv2" / "events" / "mutations.json"

_ET = ZoneInfo("America/New_York")
_UTC = timezone.utc

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
        raise RuntimeError("PQ23-B tests must never open a socket")

    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(socket, "create_connection", _refuse)
    yield


@pytest.fixture(autouse=True)
def _credential_scrub(monkeypatch: pytest.MonkeyPatch):
    for key in _CREDENTIAL_KEYS:
        monkeypatch.delenv(key, raising=False)
    yield


# ---------------------------------------------------------------------------
# Fixture loading
# ---------------------------------------------------------------------------


def _load_fixture():
    module_name = "pq23b_fixture"
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


def _et(session: date, hour: int, minute: int, second: int = 0) -> datetime:
    return datetime.combine(
        session, time(hour, minute, second), tzinfo=_ET
    )


def _write(tmp_path: Path, fx, *, identities, events, ev_id="e" * 64):
    acq = "a" * 16
    return fx.write_event_root(
        tmp_path, identities=identities, events=events, acq_id=acq, ev_id=ev_id
    ), acq, ev_id


# ---------------------------------------------------------------------------
# Semantic ids / roles
# ---------------------------------------------------------------------------


def test_frozen_semantic_ids() -> None:
    assert E.EVENT_EA_KKP1 == "event_ear_k_kp1"
    assert E.EVENT_DAYS_SINCE_EA == "event_days_since_ea"
    assert E.EVENT_RECENT_EA_PREFIX == "event_recent_ea_w"
    assert E.RECENT_EA_MIN == 2
    assert E.RECENT_EA_MAX == 504
    assert E.LOOKBACK_SESSIONS == 504
    assert E.BOUNDARY_START == time(15, 45)
    assert E.BOUNDARY_END == time(16, 0)
    assert E.SCENARIOS == ("K0", "K1")


def test_role_resolution() -> None:
    assert E.event_semantic_id("ear") == "event_ear_k_kp1"
    assert E.event_semantic_id("days_since_ea") == "event_days_since_ea"
    assert E.event_semantic_id(30) == "event_recent_ea_w30"
    assert E.event_semantic_id("recent_ea(12)") == "event_recent_ea_w12"
    assert E.event_semantic_id("event_recent_ea_w504") == "event_recent_ea_w504"
    with pytest.raises(E.EventLayerError):
        E.recent_ea_semantic_id(1)
    with pytest.raises(E.EventLayerError):
        E.recent_ea_semantic_id(505)
    with pytest.raises(E.EventLayerError):
        E.event_semantic_id("unknown_role")


def test_cause_literals_and_precedence() -> None:
    assert E.EventCause.IDENTITY_UNMAPPED == "PQV2_EVENT_IDENTITY_UNMAPPED"
    assert E.EventCause.IDENTITY_CONTRADICTED == "PQV2_EVENT_IDENTITY_CONTRADICTED"
    assert E.EventCause.SOURCE_EXCLUDED == "PQV2_EVENT_SOURCE_EXCLUDED"
    assert E.EventCause.TIME_UNAVAILABLE == "PQV2_EVENT_TIME_UNAVAILABLE"
    assert E.EventCause.RESERVE == "PQV2_EVENT_RESERVE"
    assert E.EVENT_CAUSE_PRECEDENCE == (
        E.EventCause.RESERVE,
        E.EventCause.IDENTITY_CONTRADICTED,
        E.EventCause.IDENTITY_UNMAPPED,
        E.EventCause.SOURCE_EXCLUDED,
        E.EventCause.TIME_UNAVAILABLE,
    )


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def test_load_roundtrip(tmp_path: Path, fx) -> None:
    sessions = fx.business_days(date(2021, 1, 4), 5)
    identities = [fx.make_identity("A")]
    events = [
        fx.make_event(
            "A",
            k_session=sessions[1],
            acceptance_et=_et(sessions[1], 10, 0),
            acceptance_json_raw=f"{sessions[1].isoformat()}T15:00:00.000Z",
        )
    ]
    _, acq, ev_id = _write(tmp_path, fx, identities=identities, events=events)
    layer = E.load_event_layer(tmp_path, acq_id=acq, ev_id=ev_id)
    assert layer.keys == ("A",)
    assert layer.status_by_key["A"] == "MAPPED"
    assert len(layer.events_by_key["A"]) == 1
    assert layer.events_by_key["A"][0].k_session == sessions[1]
    assert layer.events_by_key["A"][0].acceptance_et == _et(sessions[1], 10, 0)


def test_manifest_ev_id_mismatch(tmp_path: Path, fx) -> None:
    identities = [fx.make_identity("A")]
    (pair, acq, ev_id) = _write(tmp_path, fx, identities=identities, events=[])
    with pytest.raises(E.EventLayerError):
        E.load_event_layer(tmp_path, acq_id=acq, ev_id="0" * 64)


def test_missing_manifest(tmp_path: Path, fx) -> None:
    with pytest.raises(E.EventLayerError):
        E.load_event_layer(tmp_path, acq_id="a" * 16, ev_id="b" * 64)


def test_reserve_path_refused(tmp_path: Path, fx) -> None:
    reserve_root = tmp_path / "reserve_sealed"
    reserve_root.mkdir()
    with pytest.raises(E.EventFirewallError):
        E.load_event_layer(reserve_root, acq_id="a" * 16, ev_id="b" * 64)
    with pytest.raises(E.EventFirewallError):
        E.load_event_layer(
            tmp_path, acq_id="../reserve_sealed/x", ev_id="b" * 64
        )


def test_reserve_rows_refused(tmp_path: Path, fx) -> None:
    identities = [fx.make_identity("R")]
    events = [
        fx.make_event(
            "R",
            k_session=date(2023, 7, 3),
            acceptance_et=_et(date(2023, 7, 3), 10, 0),
        )
    ]
    (_, acq, ev_id) = _write(tmp_path, fx, identities=identities, events=events)
    with pytest.raises(E.EventFirewallError):
        E.load_event_layer(tmp_path, acq_id=acq, ev_id=ev_id)


def _write_package(
    tmp_path: Path, fx, *, identities, events, package: str, ev_id="e" * 64
):
    acq = "a" * 16
    return fx.write_event_root(
        tmp_path,
        identities=identities,
        events=events,
        acq_id=acq,
        ev_id=ev_id,
        package=package,
    ), acq, ev_id


def test_reserve_fold_reads_reserve_events(tmp_path: Path, fx) -> None:
    # A reserve fold opens ``reserve_sealed/edgar-<acq_id>/`` and admits the
    # reserve rows with K(e) <= E (firewall §2.2).
    session = date(2023, 7, 10)
    identities = [fx.make_identity("R", interval_end=date(2023, 7, 31))]
    events = [fx.make_event("R", k_session=session, acceptance_et=_et(session, 10, 0))]
    (_, acq, ev_id) = _write_package(
        tmp_path,
        fx,
        identities=identities,
        events=events,
        package="reserve_sealed",
    )
    fold = C.Fold("reserve", date(2023, 7, 3), date(2023, 7, 31))
    layer = E.load_event_layer(tmp_path, acq_id=acq, ev_id=ev_id, fold=fold)
    assert layer.keys == ("R",)
    assert len(layer.events_by_key["R"]) == 1
    assert layer.events_by_key["R"][0].k_session == session


def test_reserve_fold_event_at_e_admissible(tmp_path: Path, fx) -> None:
    end = date(2023, 7, 31)
    identities = [fx.make_identity("R", interval_end=end)]
    events = [fx.make_event("R", k_session=end, acceptance_et=_et(end, 10, 0))]
    (_, acq, ev_id) = _write_package(
        tmp_path,
        fx,
        identities=identities,
        events=events,
        package="reserve_sealed",
    )
    fold = C.Fold("reserve", date(2023, 7, 3), end)
    layer = E.load_event_layer(tmp_path, acq_id=acq, ev_id=ev_id, fold=fold)
    assert layer.events_by_key["R"][0].k_session == end


def test_reserve_fold_event_beyond_e_refused(tmp_path: Path, fx) -> None:
    end = date(2023, 7, 31)
    beyond = date(2023, 8, 1)
    identities = [fx.make_identity("R", interval_end=beyond)]
    events = [fx.make_event("R", k_session=beyond, acceptance_et=_et(beyond, 10, 0))]
    (_, acq, ev_id) = _write_package(
        tmp_path,
        fx,
        identities=identities,
        events=events,
        package="reserve_sealed",
    )
    fold = C.Fold("reserve", date(2023, 7, 3), end)
    with pytest.raises(E.EventFirewallError):
        E.load_event_layer(tmp_path, acq_id=acq, ev_id=ev_id, fold=fold)


def test_default_fold_reads_development_layer_not_reserve(
    tmp_path: Path, fx
) -> None:
    # Under the default fold the loader reads ``derived/edgar-<acq_id>/``; the
    # reserve package is never opened even when it carries events.
    session = date(2023, 7, 10)
    identities = [fx.make_identity("R", interval_end=date(2023, 7, 31))]
    (_, acq, ev_id) = _write_package(
        tmp_path,
        fx,
        identities=identities,
        events=(),
        package="derived",
    )
    _write_package(
        tmp_path,
        fx,
        identities=identities,
        events=[
            fx.make_event(
                "R", k_session=session, acceptance_et=_et(session, 10, 0)
            )
        ],
        package="reserve_sealed",
    )
    layer = E.load_event_layer(
        tmp_path, acq_id=acq, ev_id=ev_id, fold=C.DEVELOPMENT_FOLD
    )
    assert layer.keys == ("R",)
    assert layer.events_by_key.get("R", ()) == ()


def test_unknown_key_in_event_panel(tmp_path: Path, fx) -> None:
    identities = [fx.make_identity("A")]
    events = [
        fx.make_event("Z", k_session=date(2021, 1, 5), acceptance_et=None)
    ]
    (_, acq, ev_id) = _write(tmp_path, fx, identities=identities, events=events)
    with pytest.raises(E.EventLayerError):
        E.load_event_layer(tmp_path, acq_id=acq, ev_id=ev_id)


# ---------------------------------------------------------------------------
# Cause sources (M1b §2)
# ---------------------------------------------------------------------------


def _cause_fixture(fx, tmp_path):
    sessions = fx.business_days(date(2021, 1, 4), 3)
    identities = [
        fx.make_identity("CLEAN"),
        fx.make_identity("AMB", status="AMBIGUOUS", cik=None),
        fx.make_identity("NOFIL", status="NO_FILINGS_IN_INTERVAL", cik=None),
        fx.make_identity("NOCAND", status="NO_CANDIDATE", cik=None),
        fx.make_identity("FOREIGN", status="FOREIGN_FILER", cik=None),
        fx.make_identity("CONTRA", audit_verdict="CONTRADICTED"),
        fx.make_identity("BOTH", status="AMBIGUOUS", cik=None, audit_verdict="CONTRADICTED"),
        fx.make_identity("NULLTIME"),
    ]
    events = [
        fx.make_event(
            "NULLTIME",
            k_session=None,
            acceptance_et=None,
            acceptance_json_raw="2020-06-01T20:00:00.000Z",
            k_reason="EVENT_TIME_UNAVAILABLE",
        )
    ]
    (_, acq, ev_id) = _write(
        tmp_path, fx, identities=identities, events=events
    )
    return E.load_event_layer(tmp_path, acq_id=acq, ev_id=ev_id)


def test_each_cause_source(tmp_path: Path, fx) -> None:
    layer = _cause_fixture(fx, tmp_path)
    assert layer.key_causes("CLEAN") == ()
    assert layer.key_causes("AMB") == (E.EventCause.IDENTITY_UNMAPPED,)
    assert layer.key_causes("NOFIL") == (E.EventCause.IDENTITY_UNMAPPED,)
    assert layer.key_causes("NOCAND") == (E.EventCause.IDENTITY_UNMAPPED,)
    assert layer.key_causes("FOREIGN") == (E.EventCause.SOURCE_EXCLUDED,)
    assert layer.key_causes("CONTRA") == (E.EventCause.IDENTITY_CONTRADICTED,)
    # Precedence: CONTRADICTED before UNMAPPED (M1b §2).
    assert layer.key_causes("BOTH") == (
        E.EventCause.IDENTITY_CONTRADICTED,
        E.EventCause.IDENTITY_UNMAPPED,
    )
    # TIME_UNAVAILABLE is exposed as a source, never by key_causes.
    assert layer.key_causes("NULLTIME") == ()
    assert len(layer.time_unavailable("NULLTIME")) == 1
    # The defensive RESERVE cause leads the precedence order when a (normally
    # impossible) reserve incident is recorded on the layer.
    incident = dataclasses.replace(layer, reserve_incident=True)
    assert incident.key_causes("CLEAN") == (E.EventCause.RESERVE,)


def test_time_unavailable_scope(tmp_path: Path, fx) -> None:
    sessions = fx.business_days(date(2021, 1, 4), 3)
    identities = [
        fx.make_identity("T-IN"),
        fx.make_identity("T-OLD"),
        fx.make_identity("T-AFTER", interval_end=date(2018, 12, 31)),
        fx.make_identity("T-FPI"),
    ]
    events = [
        fx.make_event(
            "T-IN",
            k_session=None,
            acceptance_et=None,
            acceptance_json_raw="2020-06-01T20:00:00.000Z",
        ),
        fx.make_event(
            "T-OLD",
            k_session=None,
            acceptance_et=None,
            acceptance_json_raw="2014-06-01T20:00:00.000Z",
        ),
        fx.make_event(
            "T-AFTER",
            k_session=None,
            acceptance_et=None,
            acceptance_json_raw="2019-06-01T20:00:00.000Z",
        ),
        fx.make_event(
            "T-FPI",
            k_session=None,
            acceptance_et=None,
            event_type="FPI_6K",
            form="6-K",
            acceptance_json_raw="2020-06-01T20:00:00.000Z",
        ),
    ]
    (_, acq, ev_id) = _write(tmp_path, fx, identities=identities, events=events)
    layer = E.load_event_layer(tmp_path, acq_id=acq, ev_id=ev_id)
    assert len(layer.time_unavailable("T-IN")) == 1
    assert layer.time_unavailable("T-OLD") == ()
    assert layer.time_unavailable("T-AFTER") == ()
    assert layer.time_unavailable("T-FPI") == ()
    # The window helper keeps a null proxy and filters on the filing date.
    assert len(layer.time_unavailable_in_window(
        "T-IN", date(2020, 1, 1), date(2020, 12, 31)
    )) == 1
    assert layer.time_unavailable_in_window(
        "T-IN", date(2021, 1, 1), date(2021, 12, 31)
    ) == ()


def test_fpi_and_fallback_excluded(tmp_path: Path, fx) -> None:
    sessions = fx.business_days(date(2021, 1, 4), 3)
    identities = [
        fx.make_identity("F6"),
        fx.make_identity("F20"),
        fx.make_identity("FB"),
        fx.make_identity("OK"),
    ]
    events = [
        fx.make_event("F6", k_session=sessions[0], acceptance_et=_et(sessions[0], 10, 0), event_type="FPI_6K", form="6-K"),
        fx.make_event("F20", k_session=sessions[0], acceptance_et=_et(sessions[0], 10, 0), event_type="FPI_20F", form="20-F"),
        fx.make_event("FB", k_session=sessions[0], acceptance_et=_et(sessions[0], 10, 0), event_type="EA_PERIODIC_FALLBACK", form="10-Q"),
        fx.make_event("OK", k_session=sessions[0], acceptance_et=_et(sessions[0], 10, 0)),
    ]
    (_, acq, ev_id) = _write(tmp_path, fx, identities=identities, events=events)
    layer = E.load_event_layer(tmp_path, acq_id=acq, ev_id=ev_id)
    assert layer.events_by_key.get("F6", ()) == ()
    assert layer.events_by_key.get("F20", ()) == ()
    assert layer.events_by_key.get("FB", ()) == ()
    assert len(layer.events_by_key["OK"]) == 1


# ---------------------------------------------------------------------------
# B_e (M1b §3)
# ---------------------------------------------------------------------------


def test_b_e_boundary_times(fx) -> None:
    session = date(2021, 1, 15)
    sessions = [session]
    assert E.is_preclose_b_e(_et(session, 15, 44, 59), sessions) is False
    assert E.is_preclose_b_e(_et(session, 15, 45, 0), sessions) is True
    assert E.is_preclose_b_e(_et(session, 15, 59, 59), sessions) is True
    assert E.is_preclose_b_e(_et(session, 16, 0, 0), sessions) is False
    # The symmetric ±15-minute fact is not B_e: 16:00–16:15 is excluded.
    assert E.is_preclose_b_e(_et(session, 16, 14, 59), sessions) is False
    # Not on a trusted session.
    assert E.is_preclose_b_e(_et(date(2021, 1, 16), 15, 50, 0), sessions) is False
    assert E.is_preclose_b_e(None, sessions) is False


def test_b_e_dst(fx) -> None:
    winter = date(2021, 1, 15)   # EST (UTC-5)
    summer = date(2021, 7, 15)   # EDT (UTC-4)
    sessions = [winter, summer]
    # 20:50Z is 15:50 EST; 19:50Z is 15:50 EDT.
    assert E.is_preclose_b_e(datetime(2021, 1, 15, 20, 50, tzinfo=_UTC), sessions)
    assert E.is_preclose_b_e(datetime(2021, 7, 15, 19, 50, tzinfo=_UTC), sessions)
    # 21:00Z is 16:00 EST; 20:00Z is 16:00 EDT.
    assert not E.is_preclose_b_e(datetime(2021, 1, 15, 21, 0, tzinfo=_UTC), sessions)
    assert not E.is_preclose_b_e(datetime(2021, 7, 15, 20, 0, tzinfo=_UTC), sessions)


# ---------------------------------------------------------------------------
# Builder: bridge lag, observability, lookback
# ---------------------------------------------------------------------------


def _levels(sessions, increment: float = 0.01):
    return {
        "A": {session: 1.0 + increment * index for index, session in enumerate(sessions)}
    }


def _tri(fx, sessions, levels):
    return fx.tri_frame_from_levels(sessions, levels)


def test_bridge_lag_alignment_offset_labels(tmp_path: Path, fx) -> None:
    sessions = fx.business_days(date(2021, 1, 4), 10)
    levels = _levels(sessions)
    tri = _tri(fx, sessions, levels).iloc[1:]  # labels offset from the dates
    identities = [fx.make_identity("A")]
    events = [
        fx.make_event(
            "A", k_session=sessions[3], acceptance_et=_et(sessions[3], 10, 0)
        )
    ]
    (_, acq, ev_id) = _write(tmp_path, fx, identities=identities, events=events)
    layer = E.load_event_layer(tmp_path, acq_id=acq, ev_id=ev_id)
    built = E.build_event_inputs(
        layer, tri, sessions, scenario=E.SCENARIO_K0, roles=["ear", "days_since_ea"]
    )
    ear = built["event_ear_k_kp1"].values
    days = built["event_days_since_ea"].values
    row = pd.Timestamp(sessions[7])
    expected = (1.0 + 0.01 * 4) / (1.0 + 0.01 * 2) - 1.0
    assert ear.at[row, "A"] == pytest.approx(expected)
    assert days.at[row, "A"] == 3.0
    # The bridge lag is not ignored: reading the row labelled d as TRI(d) gives
    # a different value.
    naive = (1.0 + 0.01 * 3) / (1.0 + 0.01 * 1) - 1.0
    assert abs(ear.at[row, "A"] - naive) > 1e-9


def test_observability_boundary(tmp_path: Path, fx) -> None:
    sessions = fx.business_days(date(2021, 1, 4), 10)
    levels = _levels(sessions)
    tri = _tri(fx, sessions, levels)
    identities = [fx.make_identity("OBS"), fx.make_identity("EQ")]
    events = [
        fx.make_event(
            "OBS", k_session=sessions[4], acceptance_et=_et(sessions[4], 10, 0)
        ),
        # K == date(f-1): K+1 > date(f-1) -> not observed.
        fx.make_event(
            "EQ", k_session=sessions[5], acceptance_et=_et(sessions[5], 10, 0)
        ),
    ]
    (_, acq, ev_id) = _write(tmp_path, fx, identities=identities, events=events)
    layer = E.load_event_layer(tmp_path, acq_id=acq, ev_id=ev_id)
    days = E.build_event_inputs(
        layer, tri, sessions, scenario=E.SCENARIO_K0, roles=["days_since_ea"]
    )["event_days_since_ea"].values
    row = pd.Timestamp(sessions[6])
    assert days.at[row, "OBS"] == 1.0
    assert np.isnan(days.at[row, "EQ"])
    # At K+1 > date(f-1) (row sessions[5]) the OBS event is not yet observed.
    assert np.isnan(days.at[pd.Timestamp(sessions[5]), "OBS"])


def test_lookback_edge_504_and_505(tmp_path: Path, fx) -> None:
    sessions = fx.business_days(date(2021, 1, 4), 510)
    levels = {
        "L504": {s: 1.0 + 0.001 * i for i, s in enumerate(sessions)},
        "L505": {s: 1.0 + 0.001 * i for i, s in enumerate(sessions)},
    }
    tri = _tri(fx, sessions, levels)
    identities = [fx.make_identity("L504"), fx.make_identity("L505")]
    events = [
        # floor = prev(f) - 504 = position 4 (allowed).
        fx.make_event(
            "L504", k_session=sessions[4], acceptance_et=_et(sessions[4], 10, 0)
        ),
        # position 3 is 505 sessions before prev(f) (excluded).
        fx.make_event(
            "L505", k_session=sessions[3], acceptance_et=_et(sessions[3], 10, 0)
        ),
    ]
    (_, acq, ev_id) = _write(tmp_path, fx, identities=identities, events=events)
    layer = E.load_event_layer(tmp_path, acq_id=acq, ev_id=ev_id)
    days = E.build_event_inputs(
        layer, tri, sessions, scenario=E.SCENARIO_K0, roles=["days_since_ea"]
    )["event_days_since_ea"].values
    row = pd.Timestamp(sessions[509])
    assert days.at[row, "L504"] == 504.0
    assert np.isnan(days.at[row, "L505"])


def test_days_since_ea_and_recent_ea(tmp_path: Path, fx) -> None:
    sessions = fx.business_days(date(2021, 1, 4), 10)
    levels = _levels(sessions)
    tri = _tri(fx, sessions, levels)
    identities = [fx.make_identity("A")]
    events = [
        fx.make_event(
            "A", k_session=sessions[3], acceptance_et=_et(sessions[3], 10, 0)
        )
    ]
    (_, acq, ev_id) = _write(tmp_path, fx, identities=identities, events=events)
    layer = E.load_event_layer(tmp_path, acq_id=acq, ev_id=ev_id)
    built = E.build_event_inputs(
        layer, tri, sessions, scenario=E.SCENARIO_K0, roles=["days_since_ea", 2, 504]
    )
    days = built["event_days_since_ea"].values
    w2 = built["event_recent_ea_w2"].values
    w504 = built["event_recent_ea_w504"].values
    assert days.at[pd.Timestamp(sessions[5]), "A"] == 1.0
    assert days.at[pd.Timestamp(sessions[6]), "A"] == 2.0
    assert days.at[pd.Timestamp(sessions[7]), "A"] == 3.0
    assert w2.at[pd.Timestamp(sessions[5]), "A"] == 1.0
    assert w2.at[pd.Timestamp(sessions[6]), "A"] == 1.0
    assert w2.at[pd.Timestamp(sessions[7]), "A"] == 0.0
    assert w504.at[pd.Timestamp(sessions[7]), "A"] == 1.0
    # No observation yet -> NaN, not 0.
    assert np.isnan(w2.at[pd.Timestamp(sessions[4]), "A"])


# ---------------------------------------------------------------------------
# Scenario K1 (only B_e moves)
# ---------------------------------------------------------------------------


def test_k1_shifts_only_b_e_events(tmp_path: Path, fx) -> None:
    sessions = fx.business_days(date(2021, 1, 4), 12)
    levels = _levels(sessions)
    tri = _tri(fx, sessions, levels)
    identities = [
        fx.make_identity("BEE"),
        fx.make_identity("NOBEE"),
        fx.make_identity("LATE"),
    ]
    k = sessions[5]
    events = [
        fx.make_event(
            "BEE", k_session=k, acceptance_et=_et(k, 15, 50)
        ),
        fx.make_event(
            "NOBEE", k_session=k, acceptance_et=_et(k, 14, 0)
        ),
        # A 16:05 acceptance has the symmetric boundary_flag set; it is not B_e.
        fx.make_event(
            "LATE",
            k_session=k,
            acceptance_et=_et(k, 16, 5),
            boundary_flag=True,
        ),
    ]
    (_, acq, ev_id) = _write(tmp_path, fx, identities=identities, events=events)
    layer = E.load_event_layer(tmp_path, acq_id=acq, ev_id=ev_id)
    row = pd.Timestamp(sessions[9])
    k0 = E.build_event_inputs(
        layer, tri, sessions, scenario=E.SCENARIO_K0, roles=["days_since_ea"]
    )["event_days_since_ea"].values
    k1 = E.build_event_inputs(
        layer, tri, sessions, scenario=E.SCENARIO_K1, roles=["days_since_ea"]
    )["event_days_since_ea"].values
    assert k0.at[row, "BEE"] == 3.0
    assert k1.at[row, "BEE"] == 2.0  # shifted by one session
    assert k0.at[row, "NOBEE"] == 3.0
    assert k1.at[row, "NOBEE"] == 3.0
    assert k0.at[row, "LATE"] == 3.0
    assert k1.at[row, "LATE"] == 3.0


# ---------------------------------------------------------------------------
# Capability / evidence class / determinism
# ---------------------------------------------------------------------------


def test_evidence_class_and_capability(tmp_path: Path, fx) -> None:
    sessions = fx.business_days(date(2021, 1, 4), 6)
    levels = _levels(sessions)
    tri = _tri(fx, sessions, levels)
    identities = [fx.make_identity("A")]
    events = [
        fx.make_event(
            "A", k_session=sessions[1], acceptance_et=_et(sessions[1], 10, 0)
        )
    ]
    (_, acq, ev_id) = _write(tmp_path, fx, identities=identities, events=events)
    layer = E.load_event_layer(tmp_path, acq_id=acq, ev_id=ev_id)
    built = E.build_event_inputs(
        layer, tri, sessions, scenario=E.SCENARIO_K0, roles=["ear", "days_since_ea", 30]
    )
    assert set(built) == {
        "event_ear_k_kp1",
        "event_days_since_ea",
        "event_recent_ea_w30",
    }
    for semantic_id, trusted in built.items():
        assert trusted.evidence_class == EvidenceClass.CONSTRUCTED
        cap = trusted.capability
        assert cap.semantic_id == semantic_id
        assert cap.frequency.value == "daily"
        assert cap.has_knowledge_date is True
        assert cap.revision_policies == frozenset({RevisionPolicy.POINT_IN_TIME})
        assert cap.history == len(sessions)
        assert list(trusted.values.index) == list(
            pd.DatetimeIndex(pd.to_datetime(sessions))
        )


def test_determinism(tmp_path: Path, fx) -> None:
    sessions = fx.business_days(date(2021, 1, 4), 8)
    levels = _levels(sessions)
    tri = _tri(fx, sessions, levels)
    identities = [
        fx.make_identity("A"),
        fx.make_identity("FOREIGN", status="FOREIGN_FILER", cik=None),
    ]
    events = [
        fx.make_event(
            "A", k_session=sessions[2], acceptance_et=_et(sessions[2], 15, 50)
        )
    ]
    (_, acq, ev_id) = _write(tmp_path, fx, identities=identities, events=events)
    first = E.load_event_layer(tmp_path, acq_id=acq, ev_id=ev_id)
    second = E.load_event_layer(tmp_path, acq_id=acq, ev_id=ev_id)
    assert first.keys == second.keys
    assert first.events_by_key == second.events_by_key
    assert first.null_time_by_key == second.null_time_by_key
    built_a = E.build_event_inputs(
        first, tri, sessions, scenario=E.SCENARIO_K1, roles=["ear", "days_since_ea", 5]
    )
    built_b = E.build_event_inputs(
        second, tri, sessions, scenario=E.SCENARIO_K1, roles=["ear", "days_since_ea", 5]
    )
    for semantic_id in built_a:
        assert built_a[semantic_id].values.equals(built_b[semantic_id].values)


# ---------------------------------------------------------------------------
# Mutation fixture contract
# ---------------------------------------------------------------------------


def test_mutation_fixture_contract() -> None:
    mutations = json.loads(MUTATIONS.read_text(encoding="utf-8"))
    assert [mutation["id"] for mutation in mutations] == [
        "CONTROL",
        "B23-M1",
        "B23-M2",
        "B23-M3",
        "B23-M4",
        "B23-M5",
        "B23-M6",
        "B24-M4",
    ]
    source = (REPO_ROOT / "smart_beta/dataset_v1/pqv2_events.py").read_text(
        encoding="utf-8"
    )
    this_module = sys.modules[__name__]
    for mutation in mutations:
        assert mutation["file"] == "smart_beta/dataset_v1/pqv2_events.py"
        assert mutation["description"]
        module_name, _, test_name = mutation["test"].partition("::")
        assert module_name == "tests/test_pqv2_events.py"
        assert hasattr(this_module, test_name), mutation["id"]
        if mutation["id"] == "CONTROL":
            assert mutation["edits"] == []
            continue
        assert mutation["edits"], mutation["id"]
        for edit in mutation["edits"]:
            assert edit["old"] and edit["new"] is not None
            assert source.count(edit["old"]) == 1, (mutation["id"], edit["old"])
            assert edit["old"] != edit["new"], mutation["id"]
