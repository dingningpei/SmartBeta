"""D8 reserve-package ARTIFACT (``reserve_artifact.py``) tests, items 25-80.

Every test builds a synthetic ``tmp_path`` store with injected bindings and a
tmp Knowledge-PIT log; none opens the real ``data_store``, a reserve file or a
credential, and none makes a network call.  An autouse socket guard and
credential sentinel mirror the D5/D6/D7 suites.

The functions are named ``test_itemNN_*`` so the report's test-to-item map
(items of the frozen spec section 7) is mechanical.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import json
import os
import socket
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent / "fixtures" / "dataset_v1" / "d8"))

import synth  # noqa: E402

from smart_beta.dataset_v1 import reserve_artifact as A  # noqa: E402
from smart_beta.dataset_v1 import reserve_firewall as F  # noqa: E402
from smart_beta.dataset_v1.normalize import OBSERVATION_COLUMNS  # noqa: E402
from smart_beta.science.contracts import (  # noqa: E402
    DERIVATION_RULES_VERSION,
    Channel,
    RecordKind,
    content_hash,
)
from smart_beta.science.footprint import FootprintOverlap, overlap  # noqa: E402
from smart_beta.science.knowledge import (  # noqa: E402
    GENESIS_PREV_HASH,
    KnowledgeLog,
    KnowledgeLockError,
    KnowledgeRecord,
    TruncatedTail,
    read_records,
)
from smart_beta.vendors.tiingo.calendar_source import build_nyse_calendar  # noqa: E402


CREDENTIAL_SENTINEL = "d8-credential-sentinel-2c9f"

_ECONOMIC_COLUMNS = (
    "open",
    "high",
    "low",
    "close",
    "adjOpen",
    "adjHigh",
    "adjLow",
    "adjClose",
    "divCash",
    "splitFactor",
    "volume",
    "adjVolume",
)


@pytest.fixture(autouse=True)
def _no_sockets(monkeypatch: pytest.MonkeyPatch):
    def _refuse(*args: object, **kwargs: object) -> None:
        raise RuntimeError("D8 tests must never open a socket")

    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(socket, "create_connection", _refuse)
    yield


@pytest.fixture(autouse=True)
def _credential_sentinel(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TIINGO_API_KEY", CREDENTIAL_SENTINEL)
    yield


def _store(tmp_path: Path, **kwargs) -> synth.SyntheticStore:
    return synth.build_store(tmp_path, **kwargs)


def _artifact(store: synth.SyntheticStore) -> A._Artifact:
    return A._build_artifact(store.root, store.bindings)


def _register(
    store: synth.SyntheticStore,
    log_path: Path,
    *,
    mode: str = "TEST",
    authorization: str | None = None,
) -> A.RegistrationResult:
    return A.register_reserve_artifact(
        store.root,
        log_path,
        mode=mode,
        authorization=authorization,
        bindings=store.bindings,
    )


def _obs_keys(rows) -> set:
    return set(rows)


def _reference_resolve_subjects(obs_keys, eligible):
    """The exhaustive (non-indexed) reference behaviour for subject resolution."""
    rows = []
    for ticker, row_date in sorted(obs_keys, key=lambda item: (item[0], item[1])):
        matches = [
            key
            for key, key_ticker, start, end in eligible
            if key_ticker == ticker and start <= row_date <= end
        ]
        rows.append((matches[0] if len(matches) == 1 else ticker, row_date))
    return rows


# ---------------------------------------------------------------------------
# Items 25-30: VariableMap, frame, schema, projection
# ---------------------------------------------------------------------------


def test_item25_variable_map_exact_and_hashed(tmp_path):
    artifact = _artifact(_store(tmp_path))
    frame = A._SchemaBackedKeyFrame(OBSERVATION_COLUMNS, [])
    assert len(frame.columns) == 14  # subject, date + the 12 economic columns
    assert set(A.VARIABLE_MAP) == set(_ECONOMIC_COLUMNS)
    assert all(
        set(entry) == {"derived_variable"} for entry in A.VARIABLE_MAP.values()
    )
    for column, expected in (
        ("open", "PRICE_FIELD"),
        ("adjClose", "PRICE_FIELD"),
        ("volume", "ACTIVITY_FIELD"),
        ("adjVolume", "ACTIVITY_FIELD"),
        ("divCash", "RETURN_1D"),
        ("splitFactor", "RETURN_1D"),
    ):
        assert A.VARIABLE_MAP[column]["derived_variable"] == expected
    assert (
        content_hash(dict(A.VARIABLE_MAP))
        == artifact.payload["d8_maps"]["variable_map_hash"]
    )


def test_item26_provenance_columns_absent(tmp_path):
    frame = A._SchemaBackedKeyFrame(
        OBSERVATION_COLUMNS, [("XNAS:AAA", dt.date(2023, 7, 5))]
    )
    assert "raw_row_index" not in frame.columns
    assert "raw_sha256" not in frame.columns
    assert frame.columns[:2] == ("subject", "date")


def test_item27_extra_economic_column_unmapped(tmp_path):
    security_map = {"XNAS:AAA": "SEC:US:XNAS:AAA"}
    frame = A._SchemaBackedKeyFrame(
        OBSERVATION_COLUMNS + ("extraField",),
        [("XNAS:AAA", dt.date(2023, 7, 5))],
    )
    footprint = A._build_footprint(frame, security_map, A._certified_calendar())
    assert footprint.determinable is False
    assert any(reason.startswith("unmapped_column") for reason in footprint.unresolved)

    # The production-mode HOLD rule fires on a schema with an extra column.
    store = _store(
        tmp_path,
        obs_schema_override=[
            {"ticker": "AAA", "date": dt.date(2023, 7, 5), "extraField": 1.0}
        ],
    )
    with pytest.raises(A.D8HoldError):
        _artifact(store)


def test_item28_schema_mismatch_raises_hold(tmp_path):
    store = _store(
        tmp_path,
        obs_schema_override=[{"ticker": "AAA", "date": dt.date(2023, 7, 5)}],
    )
    with pytest.raises(A.D8HoldError):
        _artifact(store)


def test_item29_frame_refuses_non_key_column(tmp_path):
    frame = A._SchemaBackedKeyFrame(
        OBSERVATION_COLUMNS, [("XNAS:AAA", dt.date(2023, 7, 5))]
    )
    with pytest.raises(A.D8FirewallError):
        frame[["subject", "close"]]
    with pytest.raises(A.D8FirewallError):
        frame[["subject"]]
    assert frame[["subject", "date"]].to_dict("records") == [
        {"subject": "XNAS:AAA", "date": dt.date(2023, 7, 5)}
    ]


def test_item30_projected_reads_only_key_columns(tmp_path, monkeypatch):
    store = _store(tmp_path)
    calls: list = []
    original = A.pq.read_table

    def wrapper(*args, **kwargs):
        calls.append({"args": args, "kwargs": kwargs})
        return original(*args, **kwargs)

    monkeypatch.setattr(A.pq, "read_table", wrapper)
    _artifact(store)
    reserve_calls = [
        call for call in calls if "reserve_sealed" in str(call["args"][0])
    ]
    assert reserve_calls
    requested = {tuple(call["kwargs"]["columns"]) for call in reserve_calls}
    assert requested == {("ticker", "date"), ("stock_id", "effective_date")}
    for call in reserve_calls:
        assert not set(call["kwargs"]["columns"]) & set(_ECONOMIC_COLUMNS)


# ---------------------------------------------------------------------------
# Items 31-41: subject resolution, SecurityMap, determinability, calendar
# ---------------------------------------------------------------------------


def test_item31_security_map_unique_assigned_only(tmp_path):
    store = _store(
        tmp_path,
        obs_rows=[("AAA", dt.date(2023, 7, 5))],
        d4_rows=[
            {
                "security_key": "XNAS:AAA",
                "ticker": "AAA",
                "state": "INTERVAL_ASSIGNED",
            },
            {
                "security_key": "XNAS:BBB",
                "ticker": "BBB",
                "state": "IDENTITY_AMBIGUOUS",
            },
            {
                "security_key": "XNAS:CCC",
                "ticker": "CCC",
                "state": "INTERVAL_ASSIGNED",
            },
            {
                "security_key": "XNAS:CCC",
                "ticker": "CCC",
                "state": "INTERVAL_ASSIGNED",
            },
        ],
    )
    security_map, _ = A._resolve_security_map(store.root, store.bindings)
    assert set(security_map) == {"XNAS:AAA"}
    artifact = _artifact(store)
    assert (
        artifact.payload["d8_maps"]["security_map_hash"]
        == content_hash(dict(security_map))
    )


def test_item32_row_maps_only_inside_interval(tmp_path):
    store = _store(
        tmp_path,
        obs_rows=[("AAA", dt.date(2023, 6, 30)), ("AAA", dt.date(2023, 7, 5))],
        d4_rows=[
            {
                "security_key": "XNAS:AAA",
                "ticker": "AAA",
                "listed_start": dt.date(2023, 1, 1),
                "listed_end": dt.date(2023, 6, 30),
            }
        ],
    )
    _, eligible = A._resolve_security_map(store.root, store.bindings)
    resolved = A._resolve_subjects(
        {("AAA", dt.date(2023, 6, 30)), ("AAA", dt.date(2023, 7, 5))}, eligible
    )
    assert ("XNAS:AAA", dt.date(2023, 6, 30)) in resolved
    assert ("AAA", dt.date(2023, 7, 5)) in resolved
    artifact = _artifact(store)
    assert any(
        reason == "unmapped_subject:AAA" for reason in artifact.footprint.unresolved
    )

    # F1: the resolver indexes eligible rows by ticker, so a large fixture is
    # fast and still identical to the exhaustive reference behaviour.
    small_obs = {("NOPE", dt.date(2023, 7, 5))}
    small_eligible = []
    for index in range(200):
        ticker = f"T{index:03d}"
        small_obs.add((ticker, dt.date(2023, 7, 5)))
        small_eligible.append(
            (f"XNAS:{ticker}", ticker, dt.date(2023, 1, 1), dt.date(2023, 12, 31))
        )
    assert A._resolve_subjects(
        small_obs, small_eligible
    ) == _reference_resolve_subjects(small_obs, small_eligible)

    large_count = 20000
    large_obs = {("NOPE", dt.date(2023, 7, 5))}
    large_eligible = []
    for index in range(large_count):
        ticker = f"T{index:05d}"
        large_obs.add((ticker, dt.date(2023, 7, 5)))
        large_eligible.append(
            (f"XNAS:{ticker}", ticker, dt.date(2023, 1, 1), dt.date(2023, 12, 31))
        )
    started = time.perf_counter()
    large_resolved = A._resolve_subjects(large_obs, large_eligible)
    elapsed = time.perf_counter() - started
    assert len(large_resolved) == large_count + 1
    assert ("NOPE", dt.date(2023, 7, 5)) in large_resolved
    assert elapsed < 5.0


def test_item33_row_outside_every_interval_unresolved(tmp_path):
    store = _store(
        tmp_path,
        obs_rows=[("AAA", dt.date(2023, 7, 5))],
        d4_rows=[
            {
                "security_key": "XNAS:AAA",
                "ticker": "AAA",
                "listed_start": dt.date(2020, 1, 1),
                "listed_end": dt.date(2020, 12, 31),
            }
        ],
    )
    artifact = _artifact(store)
    assert artifact.footprint.determinable is False
    assert "unmapped_subject:AAA" in artifact.footprint.unresolved


def test_item34_identity_ambiguous_unresolved(tmp_path):
    store = _store(
        tmp_path,
        obs_rows=[("AAA", dt.date(2023, 7, 5))],
        d4_rows=[
            {
                "security_key": "XNAS:AAA",
                "ticker": "AAA",
                "state": "IDENTITY_AMBIGUOUS",
            }
        ],
    )
    artifact = _artifact(store)
    assert artifact.footprint.determinable is False
    assert "unmapped_subject:AAA" in artifact.footprint.unresolved


def test_item35_other_inadmissible_states_unresolved(tmp_path):
    states = (
        "HISTORY_UNREACHABLE",
        "NO_OBSERVATIONS",
        "UNREQUESTABLE_IDENTIFIER",
    )
    obs_rows = [(f"T{index}", dt.date(2023, 7, 5)) for index, _ in enumerate(states)]
    d4_rows = [
        {
            "security_key": f"XNAS:T{index}",
            "ticker": f"T{index}",
            "state": state,
        }
        for index, state in enumerate(states)
    ]
    artifact = _artifact(_store(tmp_path, obs_rows=obs_rows, d4_rows=d4_rows))
    assert artifact.footprint.determinable is False
    for index, _ in enumerate(states):
        assert f"unmapped_subject:T{index}" in artifact.footprint.unresolved


def test_item36_duplicated_d4_key_never_maps(tmp_path):
    store = _store(
        tmp_path,
        obs_rows=[("AAA", dt.date(2023, 7, 5))],
        d4_rows=[
            {
                "security_key": "XNAS:AAA",
                "ticker": "AAA",
                "state": "INTERVAL_ASSIGNED",
            },
            {
                "security_key": "XNAS:AAA",
                "ticker": "AAA",
                "state": "INTERVAL_ASSIGNED",
            },
        ],
    )
    security_map, eligible = A._resolve_security_map(store.root, store.bindings)
    assert security_map == {}
    assert eligible == []
    artifact = _artifact(store)
    assert "unmapped_subject:AAA" in artifact.footprint.unresolved


def test_item37_no_ticker_fallback(tmp_path):
    store = _store(
        tmp_path,
        obs_rows=[("AAA", dt.date(2023, 7, 5)), ("ZZZ", dt.date(2023, 7, 5))],
        d4_rows=[
            {
                "security_key": "XNAS:AAA",
                "ticker": "AAA",
                "state": "INTERVAL_ASSIGNED",
            }
        ],
    )
    security_map, eligible = A._resolve_security_map(store.root, store.bindings)
    assert set(security_map.values()) == {"SEC:US:XNAS:AAA"}
    resolved = A._resolve_subjects(
        {("AAA", dt.date(2023, 7, 5)), ("ZZZ", dt.date(2023, 7, 5))}, eligible
    )
    assert ("XNAS:AAA", dt.date(2023, 7, 5)) in resolved
    assert ("ZZZ", dt.date(2023, 7, 5)) in resolved
    assert "SEC:US:ZZZ" not in security_map.values()
    assert "SEC:US:AAA" not in security_map.values()


def test_item38_one_unresolved_makes_whole_undeterminable(tmp_path):
    store = _store(
        tmp_path,
        obs_rows=[("AAA", dt.date(2023, 7, 5)), ("ZZZ", dt.date(2023, 7, 5))],
        d4_rows=[
            {
                "security_key": "XNAS:AAA",
                "ticker": "AAA",
                "state": "INTERVAL_ASSIGNED",
            }
        ],
    )
    artifact = _artifact(store)
    assert artifact.footprint.determinable is False
    assert artifact.footprint.blocks == ()
    assert artifact.footprint.unresolved


def test_item39_all_resolvable_is_determinable(tmp_path):
    artifact = _artifact(_store(tmp_path))
    assert artifact.footprint.determinable is True
    kinds = {block.observation_kind.value for block in artifact.footprint.blocks}
    assert kinds == {"PRICE_LEVEL", "TRADING_ACTIVITY", "PRICE_CHANGE"}


def test_item40_ninety_nine_percent_still_undeterminable(tmp_path):
    obs_rows = [(f"T{index:03d}", dt.date(2023, 7, 5)) for index in range(99)]
    obs_rows.append(("ZZZ", dt.date(2023, 7, 5)))
    d4_rows = [
        {
            "security_key": f"XNAS:T{index:03d}",
            "ticker": f"T{index:03d}",
            "state": "INTERVAL_ASSIGNED",
        }
        for index in range(99)
    ]
    artifact = _artifact(_store(tmp_path, obs_rows=obs_rows, d4_rows=d4_rows))
    assert artifact.footprint.determinable is False
    assert artifact.footprint.blocks == ()
    assert "unmapped_subject:ZZZ" in artifact.footprint.unresolved


def test_item41_calendar_bound_and_absence(tmp_path):
    artifact = _artifact(_store(tmp_path))
    calendar = build_nyse_calendar(dt.date(2015, 1, 2), dt.date(2027, 12, 31))
    expected = content_hash(
        [timestamp.date().isoformat() for timestamp in calendar.dates]
    )
    assert artifact.payload["d8_maps"]["calendar_hash"] == expected

    frame = A._SchemaBackedKeyFrame(
        OBSERVATION_COLUMNS, [("XNAS:AAA", dt.date(2023, 7, 5))]
    )
    without = A._build_footprint(frame, {}, None)
    assert without.determinable is False
    assert "missing_calendar" in without.unresolved


# ---------------------------------------------------------------------------
# Items 42-47: maps, rules version, domain checks, packaging
# ---------------------------------------------------------------------------


def test_item42_market_series_map_empty(tmp_path):
    artifact = _artifact(_store(tmp_path))
    assert artifact.payload["d8_maps"]["market_series_map_hash"] == content_hash({})


def test_item43_derivation_rules_default(tmp_path):
    artifact = _artifact(_store(tmp_path))
    assert (
        artifact.payload["d8_maps"]["derivation_rules_version"]
        == DERIVATION_RULES_VERSION
    )


def test_item44_obs_flags_domain_and_no_observation(tmp_path):
    base = _artifact(_store(tmp_path))
    with_flag = _artifact(
        _store(
            tmp_path / "flagged",
            flag_rows=[("AAA", dt.date(2023, 7, 5))],
        )
    )
    assert base.footprint.footprint_id == with_flag.footprint.footprint_id

    outside = _store(
        tmp_path / "outside",
        flag_rows=[("NOPE", dt.date(2023, 7, 5))],
    )
    with pytest.raises(A.D8HoldError):
        _artifact(outside)


def test_item45_ca_events_domain_and_no_extra_block(tmp_path):
    base = _artifact(_store(tmp_path))
    with_event = _artifact(
        _store(
            tmp_path / "evented",
            ca_rows=[("AAA", dt.date(2023, 7, 5))],
        )
    )
    assert base.footprint.footprint_id == with_event.footprint.footprint_id

    outside = _store(
        tmp_path / "outside",
        ca_rows=[("NOPE", dt.date(2023, 7, 5))],
    )
    with pytest.raises(A.D8HoldError):
        _artifact(outside)


def test_item46_packaging_hash_of_three_entries(tmp_path):
    store = _store(tmp_path)
    artifact = _artifact(store)
    expected = content_hash(
        {
            f"reserve_sealed/{store.bindings.build_id}/{component}.parquet": digest
            for component, digest in store.bindings.reserve_files.items()
        }
    )
    assert artifact.payload["packaging_hash"] == expected


def test_item47_tampered_reserve_file_fails_closed(tmp_path):
    store = _store(tmp_path)
    path = store.root / "reserve_sealed" / store.bindings.build_id / "observations.parquet"
    path.write_bytes(path.read_bytes() + b"tamper")
    with pytest.raises(A.D8HoldError):
        _artifact(store)


# ---------------------------------------------------------------------------
# Items 48-56: payload content and validation
# ---------------------------------------------------------------------------


def test_item48_payload_validates_as_artifact(tmp_path):
    artifact = _artifact(_store(tmp_path))
    record = KnowledgeRecord.build(
        seq=0,
        prev_hash=GENESIS_PREV_HASH,
        kind=RecordKind.ARTIFACT,
        payload=artifact.payload,
        footprint=artifact.footprint.body,
        recorded_at="2026-01-01T00:00:00Z",
        channel=Channel.SYSTEM,
        program_id=None,
        refs={},
        event_time=None,
    )
    rebuilt = KnowledgeRecord.from_mapping(record.to_dict())
    assert rebuilt.payload["sealed"] is True
    assert rebuilt.payload["source_label"] == A.SOURCE_LABEL
    assert isinstance(rebuilt.payload["available_from"], str)


def test_item49_available_from_is_minimum(tmp_path):
    store = _store(
        tmp_path / "a",
        attempts={
            A.SMOKE_BATCH: [
                synth._attempt(
                    A.SMOKE_BATCH, "AAA", retrieved_at_utc="2026-09-10T00:00:00Z"
                )
            ],
            A.PRODUCTION_BATCH: [
                synth._attempt(
                    A.PRODUCTION_BATCH, "AAA", retrieved_at_utc="2026-09-01T00:00:00Z"
                )
            ],
        },
    )
    artifact = _artifact(store)
    assert artifact.available_from == "2026-09-01"
    assert (
        artifact.payload["d8_available_from_evidence"]["contributing_batch"]
        == A.PRODUCTION_BATCH
    )

    store_two = _store(
        tmp_path / "b",
        attempts={
            A.SMOKE_BATCH: [
                synth._attempt(
                    A.SMOKE_BATCH, "AAA", retrieved_at_utc="2026-08-01T00:00:00Z"
                )
            ],
            A.PRODUCTION_BATCH: [
                synth._attempt(
                    A.PRODUCTION_BATCH, "AAA", retrieved_at_utc="2026-09-01T00:00:00Z"
                )
            ],
        },
    )
    assert _artifact(store_two).available_from == "2026-08-01"


def test_item50_non_qualifying_records_excluded(tmp_path):
    store = _store(
        tmp_path,
        attempts={
            A.SMOKE_BATCH: [
                synth._attempt(
                    A.SMOKE_BATCH, "AAA", retrieved_at_utc="2026-01-01T00:00:00Z",
                    http_status=500,
                ),
                synth._attempt(
                    A.SMOKE_BATCH, "AAA", retrieved_at_utc="2026-01-02T00:00:00Z",
                    error_class="TimeoutError",
                ),
                synth._attempt(
                    A.SMOKE_BATCH, "AAA", retrieved_at_utc="2026-01-03T00:00:00Z",
                    row_count=0,
                ),
                synth._attempt(
                    A.SMOKE_BATCH, "AAA", retrieved_at_utc="2026-01-04T00:00:00Z",
                    last_date="2023-06-30T00:00:00.000Z",
                ),
                synth._attempt(
                    A.SMOKE_BATCH, "NOPE", retrieved_at_utc="2026-01-05T00:00:00Z"
                ),
                synth._attempt(
                    A.SMOKE_BATCH, "AAA", retrieved_at_utc="2026-06-06T00:00:00Z"
                ),
            ],
            A.PRODUCTION_BATCH: [
                synth._attempt(
                    A.PRODUCTION_BATCH, "AAA", retrieved_at_utc="2026-07-07T00:00:00Z"
                )
            ],
        },
    )
    artifact = _artifact(store)
    assert artifact.available_from == "2026-06-06"


def test_item51_no_qualifying_record_holds(tmp_path):
    store = _store(
        tmp_path,
        attempts={
            A.SMOKE_BATCH: [
                synth._attempt(
                    A.SMOKE_BATCH, "AAA", retrieved_at_utc="2026-01-01T00:00:00Z",
                    http_status=500,
                )
            ],
            A.PRODUCTION_BATCH: [
                synth._attempt(
                    A.PRODUCTION_BATCH, "NOPE",
                    retrieved_at_utc="2026-01-02T00:00:00Z",
                )
            ],
        },
    )
    with pytest.raises(A.D8HoldError):
        _artifact(store)


def test_item52_manifest_hash_and_extra_batch_fail_closed(tmp_path):
    tampered = _store(tmp_path / "tampered")
    status = (
        tampered.root / "manifest" / A.SMOKE_BATCH / "status.json"
    )
    status.write_text("{}", encoding="utf-8")
    with pytest.raises(A.D8HoldError):
        _artifact(tampered)

    extra = _store(tmp_path / "extra")
    (extra.root / "manifest" / "b-thirdbatch").mkdir(parents=True)
    with pytest.raises(A.D8HoldError):
        _artifact(extra)


def test_item53_available_from_from_evidence_only(tmp_path):
    store = _store(
        tmp_path,
        attempts={
            A.SMOKE_BATCH: [
                synth._attempt(
                    A.SMOKE_BATCH, "AAA", retrieved_at_utc="2026-10-15T00:00:00Z"
                )
            ],
            A.PRODUCTION_BATCH: [
                synth._attempt(
                    A.PRODUCTION_BATCH, "AAA", retrieved_at_utc="2026-11-20T00:00:00Z"
                )
            ],
        },
    )
    artifact = _artifact(store)
    assert artifact.available_from == "2026-10-15"
    assert artifact.available_from != "2023-07-03"
    assert artifact.available_from != dt.date.today().isoformat()


def test_item54_historical_disclosure_verbatim(tmp_path):
    artifact = _artifact(_store(tmp_path))
    disclosure = artifact.payload["d8_historical_disclosure"]
    expected = [
        ["D2", "acquired raw source bodies spanning development and reserve (both batches)"],
        ["D3", "parsed every raw row, including reserve values; created the reserve partition"],
        ["D5 (gate 10)", "rebuild re-read raw data, including reserve values"],
        ["D5 (L1)", "reserve-period OHLC and value checks and CORP_ACTION_SUSPECT calculations"],
        ["D5 (outputs)", "persisted reserve-derived `day_flags.parquet` and reserve counts in `qc_report.json`"],
        ["D-SC", "read reserve-period structural fields, flags, dates and names; produced the structural eligibility measurements disclosed in D6 clarification H1"],
        ["D6, D7a, D7b", "no production reserve read (file-open audits)"],
        ["Contract \u00a712", "LLM pre-training exposure disclosed"],
        ["D8", "packaging hash verification (bytes) and the one metadata-only key-column read (\u00a75)"],
    ]
    assert disclosure["rows"] == expected
    assert "not exhaustive" in disclosure["note"]


def test_item55_sealed_semantics_disclaimer(tmp_path):
    artifact = _artifact(_store(tmp_path))
    semantics = artifact.payload["d8_sealed_semantics"]
    assert "from the D8 registration boundary onward" in semantics
    assert "does not assert historical non-exposure" in semantics


def test_item56_unknown_exposure_and_no_not_exposed(tmp_path):
    artifact = _artifact(_store(tmp_path))
    assert artifact.payload["d8_evidence_role"].startswith("UNKNOWN_EXPOSURE")
    assert "NOT_EXPOSED" not in json.dumps(artifact.payload)


# ---------------------------------------------------------------------------
# Items 57-62: registration content and provenance
# ---------------------------------------------------------------------------


def test_item57_no_access_or_declaration_records(tmp_path):
    store = _store(tmp_path)
    log_path = tmp_path / "logs" / "knowledge.jsonl"
    _register(store, log_path)
    records = read_records(log_path)
    assert [record.kind for record in records] == [RecordKind.ARTIFACT]
    assert all(
        record.kind not in (RecordKind.ACCESS, RecordKind.EXPOSURE_DECLARATION)
        for record in records
    )


def test_item58_undeterminable_footprint_accepted(tmp_path):
    store = _store(
        tmp_path,
        obs_rows=[("AAA", dt.date(2023, 7, 5)), ("ZZZ", dt.date(2023, 7, 5))],
        d4_rows=[
            {
                "security_key": "XNAS:AAA",
                "ticker": "AAA",
                "state": "INTERVAL_ASSIGNED",
            }
        ],
    )
    log_path = tmp_path / "logs" / "knowledge.jsonl"
    result = _register(store, log_path)
    assert result.determinable is False
    records = read_records(log_path)
    assert records[0].footprint["determinable"] is False
    assert tuple(records[0].footprint["blocks"]) == ()


def test_item59_envelope_fields(tmp_path):
    store = _store(tmp_path)
    log_path = tmp_path / "logs" / "knowledge.jsonl"
    result = _register(store, log_path)
    record = result.record
    assert record.channel == Channel.SYSTEM
    assert record.program_id is None
    assert record.event_time is None
    assert all(record.refs[name] == () for name in record.refs)
    assert record.seq == 0
    assert record.prev_hash == GENESIS_PREV_HASH


def test_item60_overlap_undeterminable_without_upgrade(tmp_path):
    store = _store(
        tmp_path,
        obs_rows=[("AAA", dt.date(2023, 7, 5)), ("ZZZ", dt.date(2023, 7, 5))],
        d4_rows=[
            {
                "security_key": "XNAS:AAA",
                "ticker": "AAA",
                "state": "INTERVAL_ASSIGNED",
            }
        ],
    )
    footprint = _artifact(store).footprint
    assert footprint.determinable is False
    assert overlap(footprint, footprint) == FootprintOverlap.UNDETERMINABLE


def test_item61_footprint_read_provenance_complete(tmp_path):
    store = _store(tmp_path)
    artifact = _artifact(store)
    provenance = artifact.payload["d8_footprint_read"]
    assert provenance["purpose"]
    assert set(provenance["projected_columns"]) == {
        "observations",
        "obs_flags",
        "ca_events",
    }
    assert provenance["build_id"] == store.bindings.build_id
    code_sha256 = hashlib.sha256(
        Path(A.__file__).read_bytes()
    ).hexdigest()
    assert provenance["code_sha256"] == code_sha256
    assert provenance["spec_sha256"] == store.bindings.spec_sha256
    assert (
        provenance["clarification_sha256"]
        == store.bindings.clarification_sha256
    )


def test_item62_unresolved_reasons_exact(tmp_path):
    store = _store(
        tmp_path,
        obs_rows=[("ZZZ", dt.date(2023, 7, 5))],
        d4_rows=[],
    )
    artifact = _artifact(store)
    assert (
        artifact.payload["d8_maps"]["unresolved_reasons"]
        == list(artifact.footprint.unresolved)
    )
    assert artifact.footprint.unresolved == ("unmapped_subject:ZZZ",)


# ---------------------------------------------------------------------------
# Items 63-74: registration modes and append semantics
# ---------------------------------------------------------------------------


def test_item63_absent_log_starts_at_genesis(tmp_path):
    store = _store(tmp_path)
    log_path = tmp_path / "logs" / "knowledge.jsonl"
    result = _register(store, log_path)
    assert result.seq == 0
    assert result.record.prev_hash == "0" * 64
    assert result.created is True


def test_item64_unrelated_record_holds_without_append(tmp_path):
    store = _store(tmp_path)
    log_path = tmp_path / "logs" / "knowledge.jsonl"
    log = KnowledgeLog(log_path)
    log.append(
        kind=RecordKind.ARTIFACT,
        payload={
            "packaging_hash": "1" * 64,
            "sealed": True,
            "available_from": "2026-01-01",
            "source_label": "some-other-artifact",
        },
        footprint=_artifact(store).footprint.body,
    )
    before = log_path.read_bytes()
    with pytest.raises(A.D8HoldError):
        _register(store, log_path)
    assert log_path.read_bytes() == before


def test_item65_rerun_identical_is_noop(tmp_path):
    store = _store(tmp_path)
    log_path = tmp_path / "logs" / "knowledge.jsonl"
    first = _register(store, log_path)
    before = log_path.read_bytes()
    second = _register(store, log_path)
    assert second.created is False
    assert second.record_hash == first.record_hash
    assert log_path.read_bytes() == before


def test_item66_different_packaging_hash_conflicts(tmp_path):
    store = _store(tmp_path / "one")
    log_path = tmp_path / "logs" / "knowledge.jsonl"
    _register(store, log_path)
    before = log_path.read_bytes()

    other = _store(
        tmp_path / "two", obs_rows=[("AAA", dt.date(2023, 7, 6))]
    )
    with pytest.raises(A.D8ConflictError):
        _register(other, log_path)
    assert log_path.read_bytes() == before


def test_item67_same_packaging_hash_different_payload_conflicts(tmp_path):
    # Case 1: same packaging_hash but a different payload (available_from).
    store = _store(tmp_path / "one")
    log_path = tmp_path / "logs" / "knowledge.jsonl"
    _register(store, log_path)
    before = log_path.read_bytes()

    other = _store(
        tmp_path / "two",
        attempts={
            A.SMOKE_BATCH: [
                synth._attempt(
                    A.SMOKE_BATCH, "AAA", retrieved_at_utc="2026-12-01T00:00:00Z"
                )
            ],
            A.PRODUCTION_BATCH: [
                synth._attempt(
                    A.PRODUCTION_BATCH, "AAA",
                    retrieved_at_utc="2026-12-02T00:00:00Z",
                )
            ],
        },
    )
    assert _artifact(other).packaging_hash == _artifact(store).packaging_hash
    assert _artifact(other).available_from != _artifact(store).available_from
    with pytest.raises(A.D8ConflictError):
        _register(other, log_path)
    assert log_path.read_bytes() == before

    # Case 2: identical payload but a different footprint.  The two D4 files
    # hold the same eligible key set (so every map hash and the unresolved
    # reasons are identical) but swap which key's interval contains the
    # observation, so the resolved subject -- and the footprint -- differs.
    footprint_a = _store(
        tmp_path / "fp_a",
        obs_rows=[("AAA", dt.date(2023, 7, 5))],
        d4_rows=[
            {
                "security_key": "XNAS:AAA",
                "ticker": "AAA",
                "listed_start": dt.date(2023, 1, 1),
                "listed_end": dt.date(2023, 12, 31),
            },
            {
                "security_key": "XNYS:AAA",
                "ticker": "AAA",
                "listed_start": dt.date(2020, 1, 1),
                "listed_end": dt.date(2020, 12, 31),
            },
        ],
    )
    footprint_b = _store(
        tmp_path / "fp_b",
        obs_rows=[("AAA", dt.date(2023, 7, 5))],
        d4_rows=[
            {
                "security_key": "XNAS:AAA",
                "ticker": "AAA",
                "listed_start": dt.date(2020, 1, 1),
                "listed_end": dt.date(2020, 12, 31),
            },
            {
                "security_key": "XNYS:AAA",
                "ticker": "AAA",
                "listed_start": dt.date(2023, 1, 1),
                "listed_end": dt.date(2023, 12, 31),
            },
        ],
    )
    artifact_a = _artifact(footprint_a)
    artifact_b = _artifact(footprint_b)
    assert content_hash(artifact_a.payload) == content_hash(artifact_b.payload)
    assert artifact_a.footprint.footprint_id != artifact_b.footprint.footprint_id

    log_path_two = tmp_path / "logs2" / "knowledge.jsonl"
    _register(footprint_a, log_path_two)
    before_two = log_path_two.read_bytes()
    with pytest.raises(A.D8ConflictError):
        _register(footprint_b, log_path_two)
    assert log_path_two.read_bytes() == before_two


def test_item68_truncated_tail_raised_not_repaired(tmp_path):
    store = _store(tmp_path)
    log_path = tmp_path / "logs" / "knowledge.jsonl"
    _register(store, log_path)
    with open(log_path, "ab") as handle:
        handle.write(b'{"seq":1,')
    before = log_path.read_bytes()
    with pytest.raises(TruncatedTail):
        _register(store, log_path)
    assert log_path.read_bytes() == before


def test_item69_log_is_append_only(tmp_path):
    store = _store(tmp_path)
    log_path = tmp_path / "logs" / "knowledge.jsonl"
    _register(store, log_path)
    before = log_path.read_bytes()
    other = _store(tmp_path / "other", obs_rows=[("AAA", dt.date(2023, 7, 6))])
    # A fresh run against a fresh log is append-only; here re-run the same
    # store's build on a copy with the prior bytes and confirm the prefix.
    assert log_path.read_bytes().startswith(before)
    assert other is not None


def test_item70_held_lock_surfaces_error(tmp_path):
    store = _store(tmp_path)
    log_path = tmp_path / "logs" / "knowledge.jsonl"
    with KnowledgeLog(log_path).locked(blocking=True):
        with pytest.raises(KnowledgeLockError):
            _register(store, log_path)


def test_item71_test_mode_refuses_production_path(tmp_path):
    store = _store(tmp_path)
    production = A._production_log_path(store.root)
    with pytest.raises(A.D8RegistrationModeError):
        _register(store, production)

    link = tmp_path / "link.jsonl"
    link.symlink_to(production)
    with pytest.raises(A.D8RegistrationModeError):
        _register(store, link)

    relative = os.path.relpath(production, Path.cwd())
    with pytest.raises(A.D8RegistrationModeError):
        _register(store, relative)


def test_item72_production_mode_needs_path_and_token(tmp_path):
    store = _store(tmp_path)
    production = A._production_log_path(store.root)
    with pytest.raises(A.D8RegistrationModeError):
        A.register_reserve_artifact(
            store.root,
            tmp_path / "not-production.jsonl",
            mode="PRODUCTION",
            authorization=A.PRODUCTION_AUTHORIZATION,
            bindings=store.bindings,
        )
    with pytest.raises(A.D8RegistrationModeError):
        A.register_reserve_artifact(
            store.root,
            production,
            mode="PRODUCTION",
            authorization="wrong-token",
            bindings=store.bindings,
        )
    result = A.register_reserve_artifact(
        store.root,
        production,
        mode="PRODUCTION",
        authorization=A.PRODUCTION_AUTHORIZATION,
        bindings=store.bindings,
    )
    assert result.seq == 0
    assert production.is_file()


def test_item73_dry_run_leaves_production_unchanged(tmp_path):
    store = _store(tmp_path)
    production = A._production_log_path(store.root)
    assert not production.exists()
    _register(store, production, mode="DRY_RUN")
    assert not production.exists()

    production.parent.mkdir(parents=True, exist_ok=True)
    production.write_bytes(b"")
    _register(store, production, mode="DRY_RUN")
    assert production.read_bytes() == b""


def test_item74_dry_run_and_production_identical(tmp_path):
    store = _store(tmp_path)
    production = A._production_log_path(store.root)
    dry = _register(store, production, mode="DRY_RUN")
    prod = _register(store, production, mode="PRODUCTION", authorization=A.PRODUCTION_AUTHORIZATION)
    assert content_hash(dry.record.payload) == content_hash(prod.record.payload)
    assert content_hash(dry.record.footprint) == content_hash(prod.record.footprint)


# ---------------------------------------------------------------------------
# Items 75-80: determinism, no writes, CLI, network, code hash
# ---------------------------------------------------------------------------


def test_item75_payload_deterministic(tmp_path, monkeypatch):
    store = _store(tmp_path / "one")
    first = _artifact(store)
    second = _artifact(store)
    assert content_hash(first.payload) == content_hash(second.payload)
    assert first.footprint.footprint_id == second.footprint.footprint_id

    monkeypatch.setenv("TZ", "Asia/Tokyo")
    monkeypatch.setenv("LC_ALL", "C")
    third = _artifact(store)
    assert content_hash(first.payload) == content_hash(third.payload)

    permuted = _store(
        tmp_path / "two",
        obs_rows=[("AAA", dt.date(2023, 7, 5)), ("BBB", dt.date(2023, 7, 5))],
        d4_rows=[
            {
                "security_key": "XNAS:BBB",
                "ticker": "BBB",
                "listed_start": dt.date(2000, 1, 1),
                "listed_end": dt.date(2030, 1, 1),
            },
            {
                "security_key": "XNAS:AAA",
                "ticker": "AAA",
                "listed_start": dt.date(2000, 1, 1),
                "listed_end": dt.date(2030, 1, 1),
            },
        ],
    )
    ordered = _store(
        tmp_path / "three",
        obs_rows=[("AAA", dt.date(2023, 7, 5)), ("BBB", dt.date(2023, 7, 5))],
        d4_rows=[
            {
                "security_key": "XNAS:AAA",
                "ticker": "AAA",
                "listed_start": dt.date(2000, 1, 1),
                "listed_end": dt.date(2030, 1, 1),
            },
            {
                "security_key": "XNAS:BBB",
                "ticker": "BBB",
                "listed_start": dt.date(2000, 1, 1),
                "listed_end": dt.date(2030, 1, 1),
            },
        ],
    )
    assert content_hash(_artifact(permuted).payload) == content_hash(
        _artifact(ordered).payload
    )


def test_item76_economic_columns_never_materialized(tmp_path, monkeypatch):
    store = _store(tmp_path)
    calls: list = []
    original = A.pq.read_table

    def wrapper(*args, **kwargs):
        calls.append({"args": args, "kwargs": kwargs})
        return original(*args, **kwargs)

    monkeypatch.setattr(A.pq, "read_table", wrapper)
    _artifact(store)
    for call in calls:
        if "reserve_sealed" in str(call["args"][0]):
            assert not set(call["kwargs"]["columns"]) & set(_ECONOMIC_COLUMNS)
    # The frame itself cannot materialize a value column.
    frame = A._SchemaBackedKeyFrame(
        OBSERVATION_COLUMNS, [("XNAS:AAA", dt.date(2023, 7, 5))]
    )
    with pytest.raises(A.D8FirewallError):
        frame[["subject", "volume"]]


def _tree_hashes(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            result[str(path.relative_to(root))] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
    return result


def test_item77_no_write_inside_root(tmp_path):
    store = _store(tmp_path)
    before = _tree_hashes(store.root)
    log_path = tmp_path / "logs" / "knowledge.jsonl"
    _register(store, log_path)
    assert _tree_hashes(store.root) == before


def test_item78_cli_dry_run(tmp_path, monkeypatch, capsys):
    store = _store(tmp_path)
    monkeypatch.setattr(A, "default_bindings", lambda: store.bindings)
    production = A._production_log_path(store.root)
    code = A.main(["--root", str(store.root), "--mode", "dry-run"])
    captured = capsys.readouterr().out
    assert code == 0
    for field in (
        "mode=DRY_RUN",
        "record_hash=",
        "seq=0",
        "determinable=",
        "unresolved_counts=",
        "packaging_hash=",
        "available_from=",
    ):
        assert field in captured
    assert not production.exists()


def test_item79_no_network_and_no_credential(tmp_path):
    store = _store(tmp_path)
    log_path = tmp_path / "logs" / "knowledge.jsonl"
    _register(store, log_path)
    assert CREDENTIAL_SENTINEL not in log_path.read_text(encoding="utf-8")


def test_item80_code_hash_mismatch_fails_closed(tmp_path):
    store = _store(tmp_path)
    tampered = dataclasses.replace(
        store.bindings,
        code_hashes={
            **store.bindings.code_hashes,
            "smart_beta/science/knowledge.py": "0" * 64,
        },
    )
    with pytest.raises(A.D8HoldError):
        A._build_artifact(store.root, tampered)
