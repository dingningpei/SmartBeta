"""Offline boundedness + staged-capture tests for the P5A-4 Gate B recorder.

These tests exercise ``scripts/fetch_phase5a_gate_b_fixtures.py`` directly
with in-memory injected transports. They make **zero live network calls**: an
autouse tripwire replaces ``urllib.request.urlopen`` with a hard failure, and
``time.sleep`` with a hard failure as well -- so any attempt to retry or wait
through a Tiingo 429/5xx raises instead of passing.

The recorder is a non-shipped, non-test one-time script; it is imported here
by path rather than as a package module. The tests prove:

* a 429 or transient 5xx stops the probe/capture immediately, no sleep/retry;
* a transient response is never classified as a plan-tier exclusion;
* probing stops at the first transient result;
* no final universe snapshot is frozen from incomplete probe outcomes;
* HTTP-200 fixture responses are staged in a non-final area with full
  provenance (identity, symbol, endpoint, params, timestamp, status, body,
  canonical content hash);
* resume validates staged captures and requests only genuinely missing
  identities, never refetching a valid one;
* corrupted/conflicting/duplicate staging fails closed;
* an incomplete staged set can never become a canonical fixture set;
* promotion requires the exact complete expected identity set and is atomic
  for canonical consumers;
* the staging area cannot be consumed as the canonical fixture set.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import urllib.request
from pathlib import Path

import pytest

from smart_beta.vendors.tiingo.client import TiingoClient

_REPO_ROOT = Path(__file__).resolve().parent.parent
_RECORDER_PATH = _REPO_ROOT / "scripts" / "fetch_phase5a_gate_b_fixtures.py"


def _load_recorder():
    spec = importlib.util.spec_from_file_location(
        "fetch_phase5a_gate_b_fixtures_under_test", _RECORDER_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


rec = _load_recorder()

_DAILY = rec._DAILY_PATH
_PLAN_TIER_DETAIL = (
    "Error: Free and Power plans are limited to the DOW 30. If you would "
    "like access to all supported tickers, then please E-mail "
    "support@tiingo.com to get the Fundamental Data API added as an add-on "
    "service."
)


@pytest.fixture(autouse=True)
def _offline_tripwire(monkeypatch: pytest.MonkeyPatch) -> None:
    """Structurally forbid live network access and any sleep/retry wait."""

    def _boom(*args: object, **kwargs: object) -> object:
        raise AssertionError(
            "live network access is forbidden in the recorder tests"
        )

    monkeypatch.setattr(urllib.request, "urlopen", _boom)

    def _no_sleep(*args: object, **kwargs: object) -> object:
        raise AssertionError(
            "time.sleep was called: the recorder must not retry or wait"
        )

    monkeypatch.setattr(rec.time, "sleep", _no_sleep)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------
def _ok_body() -> list[dict]:
    return [{"date": rec.ENTITLEMENT_PROBE_DATE, "marketCap": 1}]


def _plan_tier_body() -> dict:
    return {"detail": _PLAN_TIER_DETAIL}


def _client(
    responses: dict[str, tuple[int, object]],
    calls: list[str] | None = None,
) -> TiingoClient:
    def transport(path: str, params: dict) -> tuple[int, object]:
        if calls is not None:
            calls.append(path)
        if path not in responses:
            raise AssertionError(f"unexpected request path {path!r}")
        return responses[path]

    return TiingoClient(transport=transport)


def _all_accessible_responses() -> dict[str, tuple[int, object]]:
    return {
        _DAILY.format(ticker=ticker): (200, _ok_body())
        for ticker in rec.CANDIDATE_TICKERS
    }


def _definitive_results() -> dict[str, dict]:
    """A synthetic all-definitive probe result set (mechanism tests only)."""
    results: dict[str, dict] = {}
    for ticker, company in rec.CANDIDATE_COMPANIES:
        if ticker in ("GOOGL", "AMZN", "NVDA", "SHW"):
            entry = rec._probe_entry(
                ticker, company, 400, _plan_tier_body(), 0
            )
        else:
            entry = rec._probe_entry(ticker, company, 200, None, 1)
        results[ticker] = entry
    return results


def _valid_snapshot() -> dict:
    return rec._build_final_snapshot(_definitive_results())


def _frozen() -> list[str]:
    return list(_valid_snapshot()["frozen_universe"])


def _write_stale_snapshot(path: Path) -> None:
    """A stale snapshot in which V/WMT are 429 and the freeze drops them."""
    results = _definitive_results()
    results["V"] = rec._probe_entry(
        "V", "Visa", 429, {"detail": "hourly allocation exhausted"}, 0
    )
    results["WMT"] = rec._probe_entry(
        "WMT", "Walmart", 429, {"detail": "hourly allocation exhausted"}, 0
    )
    path.write_text(
        json.dumps(rec._build_final_snapshot(results)), encoding="utf-8"
    )


def _staging_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, Path]:
    staging_dir = tmp_path / "staging_tiingo"
    ledger_path = tmp_path / "staging_ledger.json"
    monkeypatch.setattr(rec, "STAGING_DIR", staging_dir)
    monkeypatch.setattr(rec, "STAGING_LEDGER_PATH", ledger_path)
    return staging_dir, ledger_path


def _record_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, Path, Path]:
    snapshot_path = tmp_path / "universe_snapshot.json"
    snapshot_path.write_text(json.dumps(_valid_snapshot()), encoding="utf-8")
    fixture_dir = tmp_path / "tiingo_gate_b"
    fred_dir = tmp_path / "fred_gate_b"
    staging_dir, ledger_path = _staging_paths(tmp_path, monkeypatch)
    monkeypatch.setattr(rec, "UNIVERSE_SNAPSHOT_PATH", snapshot_path)
    monkeypatch.setattr(rec, "FIXTURE_DIR", fixture_dir)
    monkeypatch.setattr(rec, "FRED_FIXTURE_DIR", fred_dir)
    return fixture_dir, staging_dir, ledger_path


def _files(path: Path) -> list[str]:
    return sorted(p.name for p in path.iterdir()) if path.is_dir() else []


def _fixture_body(spec: dict) -> object:
    if spec["kind"] == "meta":
        return {"ticker": spec["ticker"], "name": f"{spec['ticker']} Inc."}
    if spec["kind"] == "eod":
        return [{"date": rec.GATE_B_END, "close": 1.0}]
    return [{"date": rec.ENTITLEMENT_PROBE_DATE, "marketCap": 1}]


def _client_from_specs(
    specs: list[dict],
    *,
    override: dict[str, tuple[int, object]] | None = None,
    calls: list[str] | None = None,
    params_seen: list[tuple[str, dict]] | None = None,
) -> TiingoClient:
    by_path = {spec["endpoint"]: spec for spec in specs}
    overrides = override or {}

    def transport(path: str, params: dict) -> tuple[int, object]:
        if calls is not None:
            calls.append(path)
        if params_seen is not None:
            params_seen.append((path, dict(params)))
        spec = by_path[path]
        assert dict(params) == dict(spec["params"]), (
            path,
            dict(params),
            spec["params"],
        )
        if path in overrides:
            return overrides[path]
        return (200, _fixture_body(spec))

    return TiingoClient(transport=transport)


def _seed_fred_fixture(fred_dir: Path) -> None:
    fred_dir.mkdir(parents=True, exist_ok=True)
    body_path = fred_dir / f"dgs3mo_{rec.GATE_B_START}_{rec.GATE_B_END}.json"
    body_path.write_text(
        json.dumps(
            {
                "_comment": "test",
                "raw_csv": (
                    f"observation_date,{rec._SERIES_ID}\n"
                    f"{rec.GATE_B_END},1.0\n"
                ),
                "observations": [],
            }
        ),
        encoding="utf-8",
    )
    (fred_dir / "manifest.json").write_text(
        json.dumps(
            {
                "_provenance": {
                    "live_recorded": True,
                    "series_id": rec._SERIES_ID,
                    "date_range": {
                        "start": rec.GATE_B_START,
                        "end": rec.GATE_B_END,
                    },
                },
                "recordings": {},
            }
        ),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Classification semantics
# ---------------------------------------------------------------------------
def test_classification_rules_are_exact() -> None:
    assert rec._classify_probe_status(200, None) == "accessible"
    assert (
        rec._classify_probe_status(400, _plan_tier_body())
        == "plan_tier_exclusion"
    )
    assert (
        rec._classify_probe_status(400, {"detail": "some other 400"})
        == "unexpected"
    )
    assert (
        rec._classify_probe_status(429, _plan_tier_body()) == "transient"
    )
    assert rec._classify_probe_status(503, {"detail": "DOW 30"}) == "transient"
    assert rec._classify_probe_status(500, None) == "transient"
    assert rec._classify_probe_status(418, "teapot") == "unexpected"


def test_transient_is_never_classified_as_plan_tier_exclusion() -> None:
    for status in (429, 500, 502, 503, 504):
        assert (
            rec._classify_probe_status(status, _plan_tier_body())
            == "transient"
        ), status


# ---------------------------------------------------------------------------
# Entitlement probing: bounded, stop-on-first-transient, no partial freeze
# ---------------------------------------------------------------------------
def test_429_stops_immediately_without_sleep_or_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(rec, "UNIVERSE_SNAPSHOT_PATH", tmp_path / "snap.json")
    monkeypatch.setattr(rec, "PROBE_LEDGER_PATH", tmp_path / "ledger.json")
    responses = _all_accessible_responses()
    responses[_DAILY.format(ticker="MMM")] = (
        429,
        {"detail": "hourly allocation exhausted"},
    )
    calls: list[str] = []
    assert rec.run_probe(client=_client(responses, calls)) == 3

    assert calls == [_DAILY.format(ticker="MMM")]
    assert not (tmp_path / "snap.json").exists()

    ledger = json.loads((tmp_path / "ledger.json").read_text(encoding="utf-8"))
    assert ledger["_provenance"]["status"] == "PARTIAL_NON_FINAL"
    assert ledger["definitive_results"] == {}
    assert ledger["unresolved"] == list(rec.CANDIDATE_TICKERS)
    assert ledger["blockers"][0]["classification"] == "transient"


@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_transient_5xx_stops_immediately_without_sleep_or_snapshot(
    status: int, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(rec, "UNIVERSE_SNAPSHOT_PATH", tmp_path / "snap.json")
    monkeypatch.setattr(rec, "PROBE_LEDGER_PATH", tmp_path / "ledger.json")
    responses = _all_accessible_responses()
    responses[_DAILY.format(ticker="MMM")] = (status, {"detail": "upstream"})
    calls: list[str] = []
    assert rec.run_probe(client=_client(responses, calls)) == 3
    assert calls == [_DAILY.format(ticker="MMM")]
    assert not (tmp_path / "snap.json").exists()
    ledger = json.loads((tmp_path / "ledger.json").read_text(encoding="utf-8"))
    assert ledger["blockers"][0]["classification"] == "transient"


def test_probe_stops_at_first_transient_and_keeps_prior_definitive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(rec, "UNIVERSE_SNAPSHOT_PATH", tmp_path / "snap.json")
    monkeypatch.setattr(rec, "PROBE_LEDGER_PATH", tmp_path / "ledger.json")
    responses = _all_accessible_responses()
    responses[_DAILY.format(ticker="AXP")] = (
        429,
        {"detail": "hourly allocation exhausted"},
    )
    calls: list[str] = []
    assert rec.run_probe(client=_client(responses, calls)) == 3

    assert calls == [
        _DAILY.format(ticker="MMM"),
        _DAILY.format(ticker="GOOGL"),
        _DAILY.format(ticker="AXP"),
    ]
    ledger = json.loads((tmp_path / "ledger.json").read_text(encoding="utf-8"))
    assert list(ledger["definitive_results"]) == ["MMM", "GOOGL"]
    assert ledger["unresolved"][0] == "AXP"
    assert "MMM" not in ledger["unresolved"]


def test_unexpected_status_stops_and_is_not_an_exclusion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(rec, "UNIVERSE_SNAPSHOT_PATH", tmp_path / "snap.json")
    monkeypatch.setattr(rec, "PROBE_LEDGER_PATH", tmp_path / "ledger.json")
    responses = _all_accessible_responses()
    responses[_DAILY.format(ticker="MMM")] = (403, _plan_tier_body())
    calls: list[str] = []
    assert rec.run_probe(client=_client(responses, calls)) == 3
    assert calls == [_DAILY.format(ticker="MMM")]
    assert not (tmp_path / "snap.json").exists()
    ledger = json.loads((tmp_path / "ledger.json").read_text(encoding="utf-8"))
    assert ledger["blockers"][0]["classification"] == "unexpected"
    assert ledger["definitive_results"] == {}


# ---------------------------------------------------------------------------
# Staged capture: expected identity set
# ---------------------------------------------------------------------------
def test_capture_specs_are_deterministic_complete_and_unique() -> None:
    frozen = _frozen()
    specs = rec._capture_specs(frozen)
    assert len(specs) == len(frozen) * 3 == 78
    identities = [spec["identity"] for spec in specs]
    assert len(set(identities)) == 78
    assert len(set(identities)) == len(identities)

    eod_start = rec._eod_start()
    for ticker in frozen:
        kinds = {s["kind"] for s in specs if s["ticker"] == ticker}
        assert kinds == {"meta", "eod", "fundamentals"}
    for spec in specs:
        if spec["kind"] == "meta":
            assert spec["params"] == {}
            assert spec["endpoint"] == rec._META_PATH.format(
                ticker=spec["ticker"]
            )
        elif spec["kind"] == "eod":
            assert spec["params"] == {
                "startDate": eod_start,
                "endDate": rec.GATE_B_END,
            }
            assert spec["endpoint"] == rec._EOD_PATH.format(
                ticker=spec["ticker"]
            )
        else:
            assert spec["params"] == {
                "startDate": rec.GATE_B_START,
                "endDate": rec.GATE_B_END,
            }
            assert spec["endpoint"] == rec._DAILY_PATH.format(
                ticker=spec["ticker"]
            )


def test_valid_staged_responses_zero_on_fresh_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    staging_dir, ledger_path = _staging_paths(tmp_path, monkeypatch)
    specs = rec._capture_specs(_frozen())
    assert rec._load_staged_captures(specs) == {}
    assert not staging_dir.exists()
    assert not ledger_path.exists()


# ---------------------------------------------------------------------------
# Staged capture: persistence, provenance, resume
# ---------------------------------------------------------------------------
def test_staged_200_persists_with_provenance_and_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture_dir, staging_dir, ledger_path = _record_paths(tmp_path, monkeypatch)
    specs = rec._capture_specs(_frozen())
    assert rec._run_staged_capture(_client_from_specs(specs), specs) == 0

    assert _files(staging_dir) == sorted(s["identity"] for s in specs)
    record = json.loads(
        (staging_dir / specs[0]["identity"]).read_text(encoding="utf-8")
    )
    assert record["identity"] == specs[0]["identity"]
    assert record["ticker"] == specs[0]["ticker"]
    assert record["endpoint"] == specs[0]["endpoint"]
    assert record["params"] == specs[0]["params"]
    assert record["retrieved_at_utc"]
    assert record["status_code"] == 200
    assert record["content_sha256"] == rec._content_sha256(record["body"])

    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    assert ledger["_provenance"]["status"] == "NON_FINAL_STAGING"
    assert ledger["_provenance"]["state"] == "COMPLETE_NOT_PROMOTED"
    assert ledger["_provenance"]["expected_identity_count"] == len(specs)
    assert ledger["_provenance"]["valid_captured_count"] == len(specs)
    assert set(ledger["valid_captured_identities"]) == set(
        s["identity"] for s in specs
    )
    assert ledger["remaining_identities"] == []
    assert not (fixture_dir / "manifest.json").exists()


def test_resume_skips_all_valid_without_any_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, staging_dir, _ = _record_paths(tmp_path, monkeypatch)
    specs = rec._capture_specs(_frozen())
    for spec in specs:
        rec._write_staged_capture(spec, _fixture_body(spec))

    def _must_not_request(path: str, params: dict) -> tuple[int, object]:
        raise AssertionError(f"resume requested an already-valid {path!r}")

    assert (
        rec._run_staged_capture(TiingoClient(transport=_must_not_request), specs)
        == 0
    )
    assert len(_files(staging_dir)) == len(specs)


def test_resume_requests_only_missing_identities(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _record_paths(tmp_path, monkeypatch)
    specs = rec._capture_specs(_frozen())
    for spec in specs[:5]:
        rec._write_staged_capture(spec, _fixture_body(spec))

    calls: list[str] = []
    assert rec._run_staged_capture(
        _client_from_specs(specs, calls=calls), specs
    ) == 0
    assert calls == [spec["endpoint"] for spec in specs[5:]]


def test_run_record_end_to_end_promotes_complete_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture_dir, staging_dir, _ = _record_paths(tmp_path, monkeypatch)
    _seed_fred_fixture(rec.FRED_FIXTURE_DIR)
    specs = rec._capture_specs(_frozen())
    assert rec.run_record(client=_client_from_specs(specs)) == 0

    manifest = json.loads(
        (fixture_dir / "manifest.json").read_text(encoding="utf-8")
    )
    assert set(manifest["recordings"]) == {s["identity"] for s in specs}
    assert all(
        entry["status_code"] == 200
        for entry in manifest["recordings"].values()
    )
    assert all((fixture_dir / s["identity"]).is_file() for s in specs)
    assert len(_files(staging_dir)) == len(specs)


def test_run_stage_promotes_without_touching_fred(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture_dir, _, _ = _record_paths(tmp_path, monkeypatch)

    def _boom(*args: object, **kwargs: object) -> object:
        raise AssertionError("run_stage must not fetch FRED")

    monkeypatch.setattr(rec, "_download_fred", _boom)
    specs = rec._capture_specs(_frozen())
    assert rec.run_stage(client=_client_from_specs(specs)) == 0
    assert (fixture_dir / "manifest.json").is_file()


# ---------------------------------------------------------------------------
# Staged capture: transient stop / partial-write protection
# ---------------------------------------------------------------------------
def test_transient_stops_immediately_and_preserves_prior_staged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture_dir, staging_dir, ledger_path = _record_paths(tmp_path, monkeypatch)
    specs = rec._capture_specs(_frozen())
    for spec in specs[:3]:
        rec._write_staged_capture(spec, _fixture_body(spec))

    blocked = specs[3]
    calls: list[str] = []
    client = _client_from_specs(
        specs, override={blocked["endpoint"]: (429, {"detail": "allocation"})},
        calls=calls,
    )
    assert rec._run_staged_capture(client, specs) == 3
    assert calls == [blocked["endpoint"]]

    # Prior valid staged successes survive; the failed response is not staged.
    assert all((staging_dir / s["identity"]).is_file() for s in specs[:3])
    assert not (staging_dir / blocked["identity"]).exists()

    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    assert ledger["_provenance"]["state"] == "INCOMPLETE"
    assert ledger["valid_captured_identities"] == [
        s["identity"] for s in specs[:3]
    ]
    assert blocked["identity"] not in ledger["captures"]
    assert ledger["last_blocker"]["status_code"] == 429
    assert ledger["last_blocker"]["is_transient"] is True
    assert not (fixture_dir / "manifest.json").exists()
    assert _files(fixture_dir) == []


def test_run_record_stops_on_transient_without_canonical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture_dir, _, ledger_path = _record_paths(tmp_path, monkeypatch)
    _seed_fred_fixture(rec.FRED_FIXTURE_DIR)
    specs = rec._capture_specs(_frozen())
    first = specs[0]["endpoint"]
    client = _client_from_specs(
        specs, override={first: (503, {"detail": "upstream"})}
    )
    assert rec.run_record(client=client) == 3
    assert not (fixture_dir / "manifest.json").exists()
    assert _files(fixture_dir) == []
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    assert ledger["last_blocker"]["status_code"] == 503


# ---------------------------------------------------------------------------
# Staged capture: fail-closed integrity
# ---------------------------------------------------------------------------
def test_corrupted_staged_content_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    staging_dir, _ = _staging_paths(tmp_path, monkeypatch)
    specs = rec._capture_specs(_frozen())
    rec._write_staged_capture(specs[0], _fixture_body(specs[0]))
    path = staging_dir / specs[0]["identity"]
    record = json.loads(path.read_text(encoding="utf-8"))
    record["body"] = {"tampered": True}
    path.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(SystemExit):
        rec._load_staged_captures(specs)


def test_staged_metadata_conflict_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    staging_dir, _ = _staging_paths(tmp_path, monkeypatch)
    specs = rec._capture_specs(_frozen())
    rec._write_staged_capture(specs[0], _fixture_body(specs[0]))
    path = staging_dir / specs[0]["identity"]

    record = json.loads(path.read_text(encoding="utf-8"))
    record["endpoint"] = "/wrong/endpoint"
    path.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(SystemExit):
        rec._load_staged_captures(specs)

    rec._write_staged_capture(specs[0], _fixture_body(specs[0]))
    record = json.loads(path.read_text(encoding="utf-8"))
    record["params"] = {"startDate": "1999-01-01", "endDate": "1999-12-31"}
    path.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(SystemExit):
        rec._load_staged_captures(specs)


def test_duplicate_or_unexpected_identity_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    staging_dir, _ = _staging_paths(tmp_path, monkeypatch)
    specs = rec._capture_specs(_frozen())

    staging_dir.mkdir(parents=True, exist_ok=True)
    (staging_dir / "zzz_unexpected.json").write_text("{}", encoding="utf-8")
    with pytest.raises(SystemExit):
        rec._load_staged_captures(specs)
    (staging_dir / "zzz_unexpected.json").unlink()

    with pytest.raises(SystemExit):
        rec._load_staged_captures(specs + [specs[0]])


# ---------------------------------------------------------------------------
# Promotion: exact-complete-set, atomicity, canonical isolation
# ---------------------------------------------------------------------------
def test_incomplete_staging_cannot_produce_canonical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture_dir, staging_dir, _ = _record_paths(tmp_path, monkeypatch)
    specs = rec._capture_specs(_frozen())
    for spec in specs[:5]:
        rec._write_staged_capture(spec, _fixture_body(spec))
    valid = rec._load_staged_captures(specs)
    with pytest.raises(SystemExit):
        rec._promote_staged_to_canonical(specs, valid, _frozen(), _valid_snapshot())
    assert not (fixture_dir / "manifest.json").exists()
    assert _files(fixture_dir) == []


def test_exact_complete_set_required_for_promotion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _record_paths(tmp_path, monkeypatch)
    specs = rec._capture_specs(_frozen())
    for spec in specs:
        rec._write_staged_capture(spec, _fixture_body(spec))
    valid = rec._load_staged_captures(specs)

    # Missing identity.
    incomplete = dict(valid)
    del incomplete[specs[0]["identity"]]
    with pytest.raises(SystemExit):
        rec._validate_complete_staged(specs, incomplete, _frozen(), _valid_snapshot())

    # Unexpected identity.
    extra = dict(valid)
    extra["extra.json"] = valid[specs[0]["identity"]]
    with pytest.raises(SystemExit):
        rec._validate_complete_staged(specs, extra, _frozen(), _valid_snapshot())

    # Duplicate expected identity.
    with pytest.raises(SystemExit):
        rec._validate_complete_staged(
            specs + [specs[0]], valid, _frozen(), _valid_snapshot()
        )


def test_promotion_is_atomic_and_manifest_represents_complete_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture_dir, staging_dir, _ = _record_paths(tmp_path, monkeypatch)
    specs = rec._capture_specs(_frozen())
    assert rec._run_staged_capture(_client_from_specs(specs), specs) == 0
    valid = rec._load_staged_captures(specs)
    rec._promote_staged_to_canonical(specs, valid, _frozen(), _valid_snapshot())

    manifest = json.loads(
        (fixture_dir / "manifest.json").read_text(encoding="utf-8")
    )
    assert set(manifest["recordings"]) == {s["identity"] for s in specs}
    assert all(
        entry["status_code"] == 200
        for entry in manifest["recordings"].values()
    )
    assert all((fixture_dir / s["identity"]).is_file() for s in specs)
    parent = fixture_dir.parent
    assert not (parent / (fixture_dir.name + ".promote-tmp")).exists()
    assert not (parent / (fixture_dir.name + ".promote-backup")).exists()
    # Staging remains as non-final evidence; canonical manifest is authoritative.
    assert len(_files(staging_dir)) == len(specs)


def test_promotion_failure_keeps_previous_canonical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture_dir, _, _ = _record_paths(tmp_path, monkeypatch)
    fixture_dir.mkdir(parents=True)
    previous_manifest = {
        "_provenance": {"gate_b_disposition": "RUN"},
        "recordings": {"old.json": {"url_path": "/old", "status_code": 200}},
    }
    (fixture_dir / "manifest.json").write_text(
        json.dumps(previous_manifest), encoding="utf-8"
    )
    (fixture_dir / "old.json").write_text(
        json.dumps({"_old": True}), encoding="utf-8"
    )

    specs = rec._capture_specs(_frozen())
    for spec in specs:
        rec._write_staged_capture(spec, _fixture_body(spec))
    valid = rec._load_staged_captures(specs)

    real_write = rec._write_json

    def _boom(path: Path, obj: object, **kwargs: object) -> None:
        if path.name == "manifest.json":
            raise RuntimeError("simulated promotion failure")
        real_write(path, obj, **kwargs)

    monkeypatch.setattr(rec, "_write_json", _boom)
    with pytest.raises(RuntimeError):
        rec._promote_staged_to_canonical(specs, valid, _frozen(), _valid_snapshot())

    unchanged = json.loads(
        (fixture_dir / "manifest.json").read_text(encoding="utf-8")
    )
    assert "old.json" in unchanged["recordings"]
    assert (fixture_dir / "old.json").is_file()
    parent = fixture_dir.parent
    assert not (parent / (fixture_dir.name + ".promote-tmp")).exists()
    assert not (parent / (fixture_dir.name + ".promote-backup")).exists()


def test_staging_is_not_consumable_as_canonical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture_dir, _, _ = _record_paths(tmp_path, monkeypatch)
    specs = rec._capture_specs(_frozen())
    for spec in specs:
        rec._write_staged_capture(spec, _fixture_body(spec))

    # A complete staging area with no canonical manifest is not a fixture set.
    assert not (fixture_dir / "manifest.json").exists()
    with pytest.raises(FileNotFoundError):
        rec._load_tiingo_recordings()
    with pytest.raises(SystemExit):
        rec.run_artifacts()


# ---------------------------------------------------------------------------
# Stale snapshot isolation and resume
# ---------------------------------------------------------------------------
def test_load_frozen_universe_refuses_stale_transient_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot_path = tmp_path / "universe_snapshot.json"
    _write_stale_snapshot(snapshot_path)
    monkeypatch.setattr(rec, "UNIVERSE_SNAPSHOT_PATH", snapshot_path)
    with pytest.raises(SystemExit):
        rec._load_frozen_universe()


def test_run_probe_ignores_stale_snapshot_and_reprobes_all(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot_path = tmp_path / "universe_snapshot.json"
    _write_stale_snapshot(snapshot_path)
    monkeypatch.setattr(rec, "UNIVERSE_SNAPSHOT_PATH", snapshot_path)
    monkeypatch.setattr(rec, "PROBE_LEDGER_PATH", tmp_path / "ledger.json")

    calls: list[str] = []
    responses = _all_accessible_responses()
    assert rec.run_probe(client=_client(responses, calls)) == 0
    assert calls == [
        _DAILY.format(ticker=t) for t in rec.CANDIDATE_TICKERS
    ]
    fresh = json.loads(snapshot_path.read_text(encoding="utf-8"))
    assert rec._snapshot_outcomes_are_definitive(fresh)


def test_ledger_refuses_non_definitive_or_wrong_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(rec, "PROBE_LEDGER_PATH", tmp_path / "ledger.json")

    (tmp_path / "ledger.json").write_text(
        json.dumps(
            {
                "_provenance": {"status": "PARTIAL_NON_FINAL"},
                "definitive_results": {
                    "MMM": {
                        "ticker": "MMM",
                        "status_code": 429,
                        "accessible": False,
                        "body": {"detail": "allocation"},
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(SystemExit):
        rec._load_probe_ledger()

    (tmp_path / "ledger.json").write_text(
        json.dumps(
            {"_provenance": {"status": "FINAL"}, "definitive_results": {}}
        ),
        encoding="utf-8",
    )
    with pytest.raises(SystemExit):
        rec._load_probe_ledger()


def test_seeded_ledger_is_non_final_and_traceable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(rec, "PROBE_LEDGER_PATH", tmp_path / "ledger.json")
    assert rec.run_seed_ledger() == 0
    ledger = json.loads((tmp_path / "ledger.json").read_text(encoding="utf-8"))
    provenance = ledger["_provenance"]
    assert provenance["status"] == "PARTIAL_NON_FINAL"
    assert provenance["definitive_count"] == 28
    assert provenance["unresolved"] == ["V", "WMT"]

    evidence = provenance["source_evidence"]
    log_path = _REPO_ROOT / evidence["path"]
    assert (
        hashlib.sha256(log_path.read_bytes()).hexdigest()
        == evidence["sha256"]
    )

    excluded = {
        ticker
        for ticker, entry in ledger["definitive_results"].items()
        if entry["status_code"] == 400
    }
    assert excluded == {"GOOGL", "AMZN", "NVDA", "SHW"}
    assert "V" not in ledger["definitive_results"]
    assert "WMT" not in ledger["definitive_results"]


def test_resume_probes_only_unresolved_and_finalizes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(rec, "PROBE_LEDGER_PATH", tmp_path / "ledger.json")
    monkeypatch.setattr(rec, "UNIVERSE_SNAPSHOT_PATH", tmp_path / "snap.json")
    assert rec.run_seed_ledger() == 0

    # Synthetic outcomes for the only two unresolved candidates. These are
    # mechanism probes, not a claim about what V/WMT will actually return.
    responses = {
        _DAILY.format(ticker="V"): (200, _ok_body()),
        _DAILY.format(ticker="WMT"): (200, _ok_body()),
    }
    calls: list[str] = []
    assert rec.run_probe(client=_client(responses, calls)) == 0

    assert calls == [
        _DAILY.format(ticker="V"),
        _DAILY.format(ticker="WMT"),
    ]
    assert not (tmp_path / "ledger.json").exists()
    snapshot = json.loads(
        (tmp_path / "snap.json").read_text(encoding="utf-8")
    )
    assert [
        item["ticker"] for item in snapshot["entitlement_probe"]["results"]
    ] == list(rec.CANDIDATE_TICKERS)
    assert all(
        item["status_code"] in (200, 400)
        for item in snapshot["entitlement_probe"]["results"]
    )
    accessible = {
        item["ticker"]
        for item in snapshot["entitlement_probe"]["results"]
        if item["status_code"] == 200
    }
    assert set(snapshot["frozen_universe"]) == accessible
    assert rec._snapshot_outcomes_are_definitive(snapshot)


# ---------------------------------------------------------------------------
# CLI dispatch: standalone modes must not fall through to other stages
# ---------------------------------------------------------------------------
def test_fred_mode_terminates_after_run_fred_without_artifacts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(rec, "run_fred", lambda: calls.append("fred") or 0)
    monkeypatch.setattr(
        rec, "run_artifacts", lambda: calls.append("artifacts") or 0
    )
    monkeypatch.setattr(rec.sys, "argv", ["recorder", "--mode", "fred"])
    assert rec.main() == 0
    assert calls == ["fred"]


def test_offline_mode_runs_only_artifacts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(rec, "run_fred", lambda: calls.append("fred") or 0)
    monkeypatch.setattr(
        rec, "run_artifacts", lambda: calls.append("artifacts") or 0
    )
    monkeypatch.setattr(rec.sys, "argv", ["recorder", "--mode", "offline"])
    assert rec.main() == 0
    assert calls == ["artifacts"]


# ---------------------------------------------------------------------------
# Source-level guard: no Tiingo quota-wait machinery remains
# ---------------------------------------------------------------------------
def test_recorder_source_has_no_tiingo_retry_or_quota_wait() -> None:
    source = _RECORDER_PATH.read_text(encoding="utf-8")
    assert "_MAX_RATE_LIMIT_WINDOWS" not in source
    assert "_sleep_until_next_hour" not in source
    assert "_call_with_rate_limit_backoff" not in source
    # The only retained sleep is inside the unchanged, credential-free FRED
    # download; it is not a Tiingo quota wait.
    assert source.count("time.sleep(") == 1
