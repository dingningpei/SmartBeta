"""E-4 tests — Dataset v1.3 EDGAR build: integration, gates and manifest.

The suite is offline.  An autouse guard replaces ``urllib.request.urlopen``,
``socket.socket.connect`` and ``socket.create_connection`` with a raiser and
scrubs the eight provider credentials plus ``SEC_CONTACT`` from the process
environment, so no test can reach the network.  Every input is a synthetic
fixture or an in-memory record; ``data_store/`` is never read.

Covers plan §4 (Gates and determinism, Firewall rows owner E-4): EV-C, EV-T;
the deterministic rebuild under two ``PYTHONHASHSEED``s; manifest hash
stability; and every plan §5 mutation with the ``E4`` prefix.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import textwrap
import urllib.request
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pyarrow.parquet as pq
import pytest

from smart_beta.dataset_v1 import edgar_build as B
from smart_beta.dataset_v1 import edgar_fetch as F
from smart_beta.dataset_v1.edgar_events import EventRow
from smart_beta.dataset_v1.edgar_identity import (
    AUDIT_CONFIRMED,
    AUDIT_CONTRADICTED,
    AUDIT_NOT_SAMPLED,
    GATE_FAIL,
    GATE_NOT_CERTIFIED,
    GATE_PASS,
    ROUTE_NAME,
    ROUTE_TICKER,
    STATUS_AMBIGUOUS,
    STATUS_MAPPED,
    IdentityRow,
)
from smart_beta.dataset_v1.reserve_firewall import RESERVE_START, ReserveFirewallError

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "edgar" / "build"
MUTATIONS = FIXTURES / "mutations.json"

_UTC = timezone.utc


@pytest.fixture(autouse=True)
def _offline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Forbid network egress and scrub every credential plus the contact."""

    def _refuse(*args: object, **kwargs: object) -> object:
        raise AssertionError("network access is forbidden in E-4 tests")

    monkeypatch.setattr(urllib.request, "urlopen", _refuse)
    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(socket, "create_connection", _refuse)
    for name in (*F.CREDENTIAL_VARIABLES, "SEC_CONTACT"):
        monkeypatch.delenv(name, raising=False)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _identity(
    key: str,
    *,
    status: str = STATUS_MAPPED,
    route: str | None = ROUTE_TICKER,
    start: date | None = date(2016, 1, 1),
    end: date | None = date(2020, 12, 31),
    cik: int | None = 1,
    verdict: str = AUDIT_NOT_SAMPLED,
) -> IdentityRow:
    return IdentityRow(
        security_key=key,
        cik=cik,
        status=status,
        route=route,
        interval_start=start,
        interval_end=end,
        n_verified_candidates=1 if status == STATUS_MAPPED else 0,
        audit_verdict=verdict,
    )


def _event(
    key: str,
    session: date,
    *,
    accession: str | None = None,
    event_type: str = "EA_8K202",
    cik: int = 1,
) -> EventRow:
    stamp = datetime.combine(session, time(14, 0), tzinfo=_UTC)
    return EventRow(
        security_key=key,
        cik=cik,
        accession=accession or f"{key}-{session.isoformat()}",
        form="8-K" if event_type == "EA_8K202" else "10-Q",
        event_type=event_type,
        acceptance_utc=stamp,
        acceptance_et=stamp,
        k_session=session,
        boundary_flag=False,
        report_period=None,
    )


def _returns(
    sessions: list[date],
    values: dict[str, list[float]],
) -> B.D7cDevelopmentReturns:
    frame = pd.DataFrame(
        values,
        index=pd.DatetimeIndex(pd.to_datetime(sessions)),
    )
    return B.D7cDevelopmentReturns(frame)


def _im_p_rows(
    *,
    active_evidence: int = 70,
    ended_evidence: int = 50,
    contradicted: int = 0,
) -> list[IdentityRow]:
    """100 active + 100 ended MAPPED rows with a controlled evidence base."""
    rows: list[IdentityRow] = []
    for index in range(100):
        verdict = AUDIT_NOT_SAMPLED
        if index < active_evidence:
            verdict = AUDIT_CONTRADICTED if index < contradicted else AUDIT_CONFIRMED
        rows.append(
            _identity(
                f"A{index:04d}",
                route=ROUTE_TICKER,
                verdict=verdict,
            )
        )
    for index in range(100):
        verdict = AUDIT_NOT_SAMPLED
        if index < ended_evidence:
            verdict = (
                AUDIT_CONTRADICTED
                if index < max(0, contradicted - active_evidence)
                else AUDIT_CONFIRMED
            )
        rows.append(
            _identity(
                f"E{index:04d}",
                route=ROUTE_NAME,
                start=date(2010, 1, 1),
                end=date(2018, 12, 31),
                verdict=verdict,
            )
        )
    return rows


def _full_coverage_events(
    keys=("K1", "K2"),
    years=range(2016, 2021),
    per_year: int = 3,
) -> list[EventRow]:
    events: list[EventRow] = []
    for key in keys:
        for year in years:
            for index in range(per_year):
                events.append(
                    _event(
                        key,
                        date(year, 6, 1) + timedelta(days=index),
                        accession=f"{key}-{year}-{index}",
                    )
                )
    return events


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


def test_frozen_gate_constants() -> None:
    assert B.EV_C_MEDIAN_MIN == 3.0
    assert B.EV_C_FRACTION_MIN == 0.80
    assert B.EV_T_SAMPLE_SIZE == 200
    assert B.EV_T_THRESHOLD == 0.60
    assert B.GATE_NAMES == ("IM_P", "EV_C", "EV_T")
    assert B.SCHEMA == "discovery-dataset-v1-edgar-build-v1"
    assert B.DEFAULT_IM_P_SEED == 1302
    assert B.DEFAULT_EV_T_SEED == 1304
    assert B.DEFAULT_BUILD_SEED == B.DEFAULT_EV_T_SEED


# ---------------------------------------------------------------------------
# IM-P (V§3, plan §8)
# ---------------------------------------------------------------------------


def test_im_p_gate_passes_on_clean_evidence() -> None:
    result = B.run_im_p_gate(_im_p_rows())
    assert result.status == GATE_PASS
    assert result.metrics["n_evidence_total"] == 120
    assert result.metrics["n_evidence_ended"] == 50
    assert result.metrics["contradicted_rate"] == 0.0


def test_im_p_gate_fails_on_contradicted_rate() -> None:
    result = B.run_im_p_gate(_im_p_rows(contradicted=3))
    assert result.metrics["contradicted_rate"] > 0.01
    assert result.status == GATE_FAIL


def test_im_p_gate_not_certified_below_evidence_floor() -> None:
    result = B.run_im_p_gate(_im_p_rows(active_evidence=69))
    assert result.metrics["n_evidence_total"] == 119
    assert result.metrics["evidence_sufficient"] is False
    assert result.status == GATE_NOT_CERTIFIED


# ---------------------------------------------------------------------------
# EV-C (V§4)
# ---------------------------------------------------------------------------


def test_ev_c_gate_passes_on_full_coverage() -> None:
    rows = _identity("K1"), _identity("K2")
    result = B.run_ev_c_gate(_full_coverage_events(), rows)
    assert result.status == GATE_PASS
    assert result.metrics["n_key_years"] == 10
    assert result.metrics["median_events"] == 3.0
    assert result.metrics["fraction_at_least_3"] == 1.0


def test_ev_c_gate_fails_on_low_coverage() -> None:
    rows = _identity("K1"), _identity("K2")
    result = B.run_ev_c_gate(_full_coverage_events(per_year=1), rows)
    assert result.metrics["median_events"] == 1.0
    assert result.metrics["fraction_at_least_3"] == 0.0
    assert result.status == GATE_FAIL


def test_ev_c_counts_only_mapped_domestic_keys() -> None:
    rows = (
        _identity("K1"),
        _identity("K-AMB", status=STATUS_AMBIGUOUS, route=ROUTE_TICKER),
    )
    result = B.run_ev_c_gate(_full_coverage_events(keys=("K1", "K-AMB")), rows)
    assert result.metrics["n_mapped_keys"] == 1
    # Only K1's five key-years enter the denominator.
    assert result.metrics["n_key_years"] == 5
    assert result.status == GATE_PASS


def test_ev_c_not_certified_without_key_years() -> None:
    result = B.run_ev_c_gate([], [_identity("K-AMB", status=STATUS_AMBIGUOUS)])
    assert result.status == GATE_NOT_CERTIFIED


# ---------------------------------------------------------------------------
# EV-T (V§4)
# ---------------------------------------------------------------------------


def _ev_t_world(*, spike: float, count: int = 200):
    sessions = [date(2017, 1, 1) + timedelta(days=i) for i in range(400)]
    values = {f"K{i:04d}": [0.01] * 400 for i in range(count)}
    events: list[EventRow] = []
    for index in range(count):
        key = f"K{index:04d}"
        event_session = sessions[200 + index]
        values[key][200 + index] = spike
        events.append(
            _event(key, event_session, accession=f"A{index:04d}")
        )
    return _returns(sessions, values), events


def test_ev_t_gate_passes_when_information_session_spikes() -> None:
    returns, events = _ev_t_world(spike=0.10)
    result = B.run_ev_t_gate(events, returns)
    assert result.metrics["n_usable"] == 200
    assert result.metrics["fraction"] == 1.0
    assert result.status == GATE_PASS


def test_ev_t_gate_fails_when_information_session_is_quiet() -> None:
    returns, events = _ev_t_world(spike=0.001)
    result = B.run_ev_t_gate(events, returns)
    assert result.metrics["fraction"] == 0.0
    assert result.status == GATE_FAIL


def test_ev_t_sample_excludes_reserve_events() -> None:
    sessions = [date(2023, 6, 1) + timedelta(days=i) for i in range(40)]
    returns = _returns(sessions, {"K1": [0.01] * 40})
    events = [
        _event("K1", date(2023, 6, 2), accession="A-DEV"),
        _event("K1", RESERVE_START, accession="A-RESERVE"),
    ]
    result = B.run_ev_t_gate(events, returns)
    assert result.metrics["sample_size"] == 1
    assert result.metrics["sample_sha256"] == B.sha256_hex(
        B.canonical_json(["A-DEV"])
    )


def test_ev_t_gate_not_certified_when_returns_missing() -> None:
    returns, events = _ev_t_world(spike=0.10)
    # Drop every trailing observation: the current return alone is not enough.
    narrowed = B.D7cDevelopmentReturns(
        returns._values.iloc[-1:].copy()
    )
    result = B.run_ev_t_gate(events, narrowed)
    assert result.status == GATE_NOT_CERTIFIED


def test_ev_t_sample_is_order_invariant() -> None:
    """The seeded sample is taken over the sorted panel (mutation E4-M2)."""
    sessions = [date(2017, 1, 1) + timedelta(days=i) for i in range(400)]
    values: dict[str, list[float]] = {}
    events: list[EventRow] = []
    for index in range(400):
        key = f"K{index:04d}"
        values[key] = [0.01] * 400
        # Every event session spikes; only the sampled key set can differ.
        values[key][200] = 0.10
        events.append(_event(key, sessions[200], accession=f"A{index:04d}"))
    returns = _returns(sessions, values)

    forward = B.run_ev_t_gate(events, returns)
    backward = B.run_ev_t_gate(list(reversed(events)), returns)
    assert forward.metrics == backward.metrics
    assert forward.status == backward.status
    assert forward.status == GATE_PASS


def test_d7c_returns_refuses_the_reserve() -> None:
    """EV-T fails closed on a sealed-reserve session (mutation E4-M3)."""
    sessions = [date(2023, 6, 29), RESERVE_START]
    returns = _returns(sessions, {"K1": [0.01, 0.10]})
    with pytest.raises(ReserveFirewallError):
        returns.absolute_return("K1", RESERVE_START)
    with pytest.raises(ReserveFirewallError):
        returns.trailing_absolute_returns("K1", RESERVE_START)
    # A development session is still readable.
    assert returns.absolute_return("K1", date(2023, 6, 29)) == pytest.approx(0.01)


# ---------------------------------------------------------------------------
# Build / manifest / determinism
# ---------------------------------------------------------------------------


def _build_inputs():
    rows = [_identity("K1"), _identity("K2")]
    events = _full_coverage_events()
    all_sessions = sorted({event.k_session for event in events})
    frame = pd.DataFrame(
        {"K1": [0.10] * len(all_sessions), "K2": [0.10] * len(all_sessions)},
        index=pd.DatetimeIndex(pd.to_datetime(all_sessions)),
    )
    return rows, events, B.D7cDevelopmentReturns(frame)


def test_gate_failure_is_recorded_not_ignored(tmp_path: Path) -> None:
    """A failing gate is reported as FAIL in the manifest (mutation E4-M1)."""
    rows = [_identity("K1")]
    events = _full_coverage_events(keys=("K1",), per_year=1)
    sessions = sorted({event.k_session for event in events})
    returns = _returns(sessions, {"K1": [0.10] * len(sessions)})
    manifest = B.build(
        tmp_path,
        "acq",
        identity_rows=rows,
        events=events,
        returns=returns,
        write=False,
    )
    assert manifest["gates"]["EV_C"]["status"] == GATE_FAIL
    assert manifest["gates"]["IM_P"]["status"] == GATE_NOT_CERTIFIED


def test_build_manifest_is_deterministic(tmp_path: Path) -> None:
    rows, events, returns = _build_inputs()
    first = B.build(
        tmp_path, "acq", identity_rows=rows, events=events, returns=returns
    )
    manifest_path = tmp_path / "derived" / "edgar-acq" / "manifest.json"
    first_bytes = manifest_path.read_bytes()

    second = B.build(
        tmp_path,
        "acq",
        identity_rows=list(reversed(rows)),
        events=list(reversed(events)),
        returns=returns,
    )
    assert first == second
    assert manifest_path.read_bytes() == first_bytes


def test_build_ev_id_is_the_canonical_manifest_hash(tmp_path: Path) -> None:
    rows, events, returns = _build_inputs()
    manifest = B.build(
        tmp_path, "acq", identity_rows=rows, events=events, returns=returns
    )
    payload = {key: value for key, value in manifest.items() if key != "ev_id"}
    assert manifest["ev_id"] == B.compute_ev_id(payload)


def test_build_writes_development_events_only(tmp_path: Path) -> None:
    rows = [_identity("K1")]
    development = _event("K1", date(2022, 6, 1), accession="A-DEV")
    reserve = _event("K1", RESERVE_START, accession="A-RESERVE")
    sessions = [date(2022, 5, 31), date(2022, 6, 1)]
    returns = _returns(sessions, {"K1": [0.01, 0.10]})
    manifest = B.build(
        tmp_path,
        "acq",
        identity_rows=rows,
        events=[reserve, development],
        returns=returns,
    )
    assert manifest["counts"]["development_events"] == 1
    assert manifest["counts"]["reserve_events"] == 1

    development_path = tmp_path / "derived" / "edgar-acq" / "events.parquet"
    frame = pq.read_table(development_path).to_pandas()
    assert [value for value in frame["k_session"]] == [date(2022, 6, 1)]
    reserve_path = tmp_path / "reserve_sealed" / "edgar-acq" / "events.parquet"
    reserve_frame = pq.read_table(reserve_path).to_pandas()
    assert [value for value in reserve_frame["k_session"]] == [RESERVE_START]


def test_build_reads_emitted_artifacts(tmp_path: Path) -> None:
    rows, events, returns = _build_inputs()
    B.build(tmp_path, "acq", identity_rows=rows, events=events, returns=returns)
    # A second build with no rows/events re-reads the emitted E-2/E-3 artifacts.
    replayed = B.build(tmp_path, "acq", returns=returns)
    assert replayed["counts"]["events"] == len(events)
    assert replayed["counts"]["identity_rows"] == len(rows)


def test_build_derives_from_the_raw_acquisition(tmp_path: Path) -> None:
    raw = tmp_path / "raw" / "edgar-acq"
    raw.parent.mkdir(parents=True)
    shutil.copytree(FIXTURES / "raw", raw)
    keys = [
        {
            "security_key": "K1",
            "ticker": "AAA",
            "interval_start": date(2016, 1, 1),
            "interval_end": date(2020, 12, 31),
        }
    ]
    event_session = date(2016, 3, 16)
    returns = _returns(
        [date(2016, 3, 15), event_session],
        {"K1": [0.01, 0.10]},
    )
    manifest = B.build(tmp_path, "acq", keys=keys, returns=returns)
    assert manifest["counts"]["mapped_keys"] == 1
    assert manifest["counts"]["ea_8k202_events"] == 1
    identity_path = tmp_path / "derived" / "edgar-acq" / "identity_map.parquet"
    assert identity_path.exists()


def _raw_keys() -> list[dict]:
    return [
        {
            "security_key": "K1",
            "ticker": "AAA",
            "interval_start": date(2016, 1, 1),
            "interval_end": date(2020, 12, 31),
        }
    ]


def test_build_derives_im_p_evidence_from_the_raw_store(tmp_path: Path) -> None:
    """The raw build reads the IM-P evidence from the E-1 symbol store."""
    raw = tmp_path / "raw" / "edgar-acq"
    raw.parent.mkdir(parents=True)
    shutil.copytree(FIXTURES / "raw", raw)
    returns = _returns([date(2016, 3, 14), date(2016, 3, 15)], {"K1": [0.01, 0.10]})
    B.build(tmp_path, "acq", keys=_raw_keys(), returns=returns)
    frame = pq.read_table(
        tmp_path / "derived" / "edgar-acq" / "identity_map.parquet"
    ).to_pandas()
    verdicts = dict(zip(frame["security_key"], frame["audit_verdict"]))
    # The stored symbol_evidence/1/.../aaa-cover.htm carries ``dei:TradingSymbol``
    # ``AAA`` and the D4 ticker is ``AAA``: the raw path must stamp CONFIRMED.
    assert verdicts["K1"] == AUDIT_CONFIRMED


def test_build_event_times_come_from_the_headers(tmp_path: Path) -> None:
    """K(e) is the index-header time, not the submissions-JSON time (E4-M5).

    The stored 8-K header accepts at 14:30 ET (2016-03-15, inside the
    session) while its JSON ``acceptanceDateTime`` is 16:30 ET (after the
    close, 2016-03-16).  Omitting the header mapping falls back to the JSON
    and would move K(e) a day forward.
    """
    raw = tmp_path / "raw" / "edgar-acq"
    raw.parent.mkdir(parents=True)
    shutil.copytree(FIXTURES / "raw", raw)
    returns = _returns([date(2016, 3, 14), date(2016, 3, 15)], {"K1": [0.01, 0.10]})
    manifest = B.build(tmp_path, "acq", keys=_raw_keys(), returns=returns)
    assert manifest["seeds"] == {"im_p_seed": 1302, "ev_t_seed": 1304}
    frame = pq.read_table(
        tmp_path / "derived" / "edgar-acq" / "events.parquet"
    ).to_pandas()
    ea8k = frame[frame["event_type"] == "EA_8K202"]
    assert len(ea8k) == 1
    assert ea8k.iloc[0]["acceptance_json_raw"] == "2016-03-15T20:30:00.000Z"
    assert ea8k.iloc[0]["k_session"] == date(2016, 3, 15)


def test_im_p_sample_uses_the_im_p_seed(tmp_path: Path, monkeypatch) -> None:
    """The raw IM-P sample is selected with ``im_p_seed``, not ``ev_t_seed``.

    Mutation E4-M4 passes the EV-T seed to ``_derive_identity_rows``; the
    captured sample then becomes the 1304 selection, and this test fails.
    Only the raw store reads and the identity resolution are stubbed (the
    sample selection, seeding and IM-P wiring stay real).
    """
    rows = [_identity(f"A{index:04d}", route=ROUTE_TICKER) for index in range(105)]
    rows += [
        _identity(
            f"E{index:04d}",
            route=ROUTE_NAME,
            start=date(2010, 1, 1),
            end=date(2018, 12, 31),
        )
        for index in range(105)
    ]
    monkeypatch.setattr(
        B,
        "load_edgar_reference",
        lambda root: SimpleNamespace(
            company_tickers={},
            cik_lookup={},
            submissions={},
            issuer_names={},
        ),
    )
    monkeypatch.setattr(B, "resolve_identity", lambda keys, **kwargs: list(rows))
    captured: dict[str, list[str]] = {}

    def _fake_symbol_evidence(raw_root: Path, sample_rows: list[dict]) -> dict:
        captured["sample"] = [str(row["security_key"]) for row in sample_rows]
        return {}

    monkeypatch.setattr(B, "build_symbol_evidence", _fake_symbol_evidence)
    keys = [
        {"security_key": row.security_key, "ticker": row.security_key}
        for row in rows
    ]
    B.build(
        tmp_path,
        "acq",
        keys=keys,
        events=[],
        returns=_returns([], {}),
        write=False,
    )
    assert captured["sample"] == list(B.select_im_p_sample(rows, seed=1302))


def test_build_manifest_hash_stable_across_hash_seeds(tmp_path: Path) -> None:
    script = textwrap.dedent(
        """
        import pandas as pd
        from datetime import date, datetime, time, timedelta, timezone
        from smart_beta.dataset_v1 import edgar_build as B
        from smart_beta.dataset_v1.edgar_events import EventRow
        from smart_beta.dataset_v1.edgar_identity import IdentityRow, STATUS_MAPPED, ROUTE_TICKER

        rows = [IdentityRow("K1", 1, STATUS_MAPPED, ROUTE_TICKER,
                            date(2016, 1, 1), date(2018, 12, 31), 1)]
        events = []
        sessions = []
        for year in (2016, 2017, 2018):
            day = date(year, 6, 1)
            sessions.append(day)
            events.append(EventRow("K1", 1, f"A{year}", "8-K", "EA_8K202",
                                   datetime.combine(day, time(14), tzinfo=timezone.utc),
                                   datetime.combine(day, time(10), tzinfo=timezone.utc),
                                   day, False, None))
        frame = pd.DataFrame(
            {"K1": [0.10] * len(sessions)},
            index=pd.DatetimeIndex(pd.to_datetime(sessions)),
        )
        manifest = B.build("/nonexistent", "acq", identity_rows=rows,
                           events=events, returns=B.D7cDevelopmentReturns(frame),
                           write=False)
        print(manifest["ev_id"])
        """
    )
    script_path = tmp_path / "probe.py"
    script_path.write_text(script, encoding="utf-8")

    def _run(seed: str) -> str:
        env = dict(os.environ)
        env["PYTHONHASHSEED"] = seed
        env["PYTHONPATH"] = str(REPO_ROOT)
        completed = subprocess.run(
            [sys.executable, str(script_path)],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        return completed.stdout.strip()

    assert _run("0") == _run("12345")


# ---------------------------------------------------------------------------
# Mutation fixture contract
# ---------------------------------------------------------------------------


def test_mutation_fixture_contract() -> None:
    mutations = json.loads(MUTATIONS.read_text(encoding="utf-8"))
    assert [mutation["id"] for mutation in mutations] == [
        "CONTROL",
        "E4-M1",
        "E4-M2",
        "E4-M3",
        "E4-M4",
        "E4-M5",
    ]
    source = (
        REPO_ROOT / "smart_beta" / "dataset_v1" / "edgar_build.py"
    ).read_text(encoding="utf-8")
    this_module = sys.modules[__name__]
    for mutation in mutations:
        assert mutation["file"] == "smart_beta/dataset_v1/edgar_build.py"
        assert mutation["description"]
        module_name, _, test_name = mutation["test"].partition("::")
        assert module_name == "tests/test_edgar_build.py"
        assert hasattr(this_module, test_name), mutation["id"]
        if mutation["id"] == "CONTROL":
            assert mutation["edits"] == []
            continue
        assert mutation["edits"], mutation["id"]
        for edit in mutation["edits"]:
            assert edit["old"] and edit["new"] is not None
            assert source.count(edit["old"]) == 1, (mutation["id"], edit["old"])
            assert edit["old"] != edit["new"], mutation["id"]
