"""Offline tests for the generic P5B-5 staged/resumable capture framework.

``scripts/staged_capture.py`` is exercised with in-memory injected transports
only. These tests make **zero live network calls**: an autouse tripwire
replaces ``socket.socket`` and ``time.sleep`` with hard failures, and a
source-level guard asserts the module imports no network client and no sleep.
Any attempt to reach the network, retry, or wait raises instead of passing.

The module is a non-shipped, non-test script library; it is imported here by
path rather than as a package module, mirroring
``tests/test_fetch_phase5a_gate_b_recorder.py``.

Proven:
* request identities are deterministic and independent of param order;
* credential-like request/metadata/context keys are refused;
* staged successes carry full provenance and a canonical content hash;
* resume validates staged captures and requests only genuinely missing
  identities;
* one authorized request is exactly one transport attempt: a transient or
  unexpected response stops the run immediately with no retry/sleep;
* raw evidence is preserved even when invalid (blocker bodies, append-only);
* corrupted bodies, metadata conflicts, duplicate/unexpected identities, and
  wrong-status or mismatched ledgers all fail closed;
* ingest detects same-identity body conflicts instead of overwriting;
* promotion requires the exact complete expected identity set and is atomic;
* a failed promotion leaves the previous canonical set intact;
* staging is never consumable as a canonical set.
"""

from __future__ import annotations

import importlib.util
import json
import socket
import sys
import time
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_MODULE_PATH = _REPO_ROOT / "scripts" / "staged_capture.py"


def _load_module():
    name = "staged_capture_under_test"
    spec = importlib.util.spec_from_file_location(name, _MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # dataclass() resolves the defining module through sys.modules, so the
    # dynamically loaded module must be registered before execution.
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


mod = _load_module()


@pytest.fixture(autouse=True)
def _offline_tripwire(monkeypatch: pytest.MonkeyPatch) -> None:
    """Structurally forbid live network access and any sleep/retry wait."""

    def _boom(*args: object, **kwargs: object) -> object:
        raise AssertionError(
            "live network/sleep is forbidden in staged-capture tests"
        )

    monkeypatch.setattr(socket, "socket", _boom)
    monkeypatch.setattr(time, "sleep", _boom)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _specs(n: int) -> list:
    return [
        mod.RequestSpec.build(
            endpoint=f"/api/endpoint_{i}",
            params={
                "code": f"code_{i}",
                "start": "2020-01-01",
                "end": "2020-12-31",
            },
            label=f"label_{i}",
            metadata={"role": f"role_{i}"},
            canonical_name=f"canonical_{i}.json",
        )
        for i in range(n)
    ]


def _body(spec) -> dict:
    return {"endpoint": spec.endpoint, "rows": [1, 2, 3], "code": spec.params["code"]}


def _capture(tmp_path: Path, specs: list, **kwargs) -> "mod.StagedCaptureSet":
    return mod.StagedCaptureSet(
        specs,
        staging_dir=tmp_path / "staging",
        ledger_path=tmp_path / "staging_ledger.json",
        canonical_dir=tmp_path / "canonical",
        **kwargs,
    )


def _transport(responses: dict, calls: list | None = None):
    def transport(endpoint: str, params: dict):
        if calls is not None:
            calls.append(endpoint)
        if endpoint not in responses:
            raise AssertionError(f"unexpected request endpoint {endpoint!r}")
        return responses[endpoint]

    return transport


def _all_ok(specs: list) -> dict:
    return {spec.endpoint: (200, _body(spec)) for spec in specs}


def _read_ledger(capture) -> dict:
    return json.loads(capture.ledger_path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Deterministic identities
# ---------------------------------------------------------------------------
def test_request_identity_is_deterministic_and_param_order_independent() -> None:
    a = mod.request_identity("/api/x", {"b": 2, "a": 1})
    b = mod.request_identity("/api/x", {"a": 1, "b": 2})
    assert a == b
    assert mod.request_identity("/api/y", {"a": 1, "b": 2}) != a
    assert mod.request_identity("/api/x", {"a": 1, "b": 3}) != a
    assert mod.RequestSpec.build("/api/x", {"a": 1, "b": 2}).identity == a


def test_label_and_metadata_do_not_affect_identity() -> None:
    a = mod.RequestSpec.build("/api/x", {"a": 1}, label="one", metadata={"m": 1})
    b = mod.RequestSpec.build("/api/x", {"a": 1}, label="two", metadata={"m": 2})
    assert a.identity == b.identity


def test_content_hash_is_order_independent() -> None:
    assert mod.content_sha256({"a": 1, "b": [1, 2]}) == mod.content_sha256(
        {"b": [1, 2], "a": 1}
    )
    assert mod.content_sha256({"a": 1}) != mod.content_sha256({"a": 2})


# ---------------------------------------------------------------------------
# Credentials never committed
# ---------------------------------------------------------------------------
def test_credential_like_params_are_refused() -> None:
    with pytest.raises(mod.StagedCaptureError):
        mod.RequestSpec.build("/api/x", {"api_key": "abc123"})
    with pytest.raises(mod.StagedCaptureError):
        mod.RequestSpec.build("/api/x", {"Authorization": "Bearer abc"})


def test_credential_like_metadata_is_refused() -> None:
    with pytest.raises(mod.StagedCaptureError):
        mod.RequestSpec.build("/api/x", {"a": 1}, metadata={"token": "abc"})


def test_credential_like_context_is_refused(tmp_path: Path) -> None:
    with pytest.raises(mod.StagedCaptureError):
        _capture(tmp_path, _specs(1), context={"password": "abc"})


# ---------------------------------------------------------------------------
# Set construction fails closed
# ---------------------------------------------------------------------------
def test_duplicate_request_identity_fails_closed(tmp_path: Path) -> None:
    spec = _specs(1)[0]
    with pytest.raises(mod.StagedCaptureError):
        _capture(tmp_path, [spec, spec])


def test_duplicate_canonical_name_fails_closed(tmp_path: Path) -> None:
    first = mod.RequestSpec.build(
        "/api/a", {"x": 1}, canonical_name="same.json"
    )
    second = mod.RequestSpec.build(
        "/api/b", {"x": 2}, canonical_name="same.json"
    )
    with pytest.raises(mod.StagedCaptureError):
        _capture(tmp_path, [first, second])


# ---------------------------------------------------------------------------
# Staging provenance + ledger
# ---------------------------------------------------------------------------
def test_staged_success_writes_provenance_and_hash(tmp_path: Path) -> None:
    specs = _specs(3)
    capture = _capture(tmp_path, specs)
    assert capture.run(_transport(_all_ok(specs))) == 0

    valid = capture.load_valid()
    assert set(valid) == {spec.identity for spec in specs}
    record = valid[specs[0].identity]
    assert record["identity"] == specs[0].identity
    assert record["endpoint"] == specs[0].endpoint
    assert record["params"] == dict(specs[0].params)
    assert record["label"] == specs[0].label
    assert record["metadata"] == dict(specs[0].metadata)
    assert record["retrieved_at_utc"]
    assert record["status_code"] == 200
    assert record["body_format"] == "json-canonical"
    assert record["content_sha256"] == mod.content_sha256(record["body"])

    ledger = _read_ledger(capture)
    assert ledger["_provenance"]["status"] == "NON_FINAL_STAGING"
    assert ledger["_provenance"]["state"] == "COMPLETE_NOT_PROMOTED"
    assert ledger["_provenance"]["expected_identity_count"] == len(specs)
    assert ledger["_provenance"]["valid_captured_count"] == len(specs)
    assert ledger["remaining_identities"] == []
    assert set(ledger["captures"]) == {spec.identity for spec in specs}
    assert capture.is_complete() is True


def test_missing_staging_dir_is_empty_resume(tmp_path: Path) -> None:
    capture = _capture(tmp_path, _specs(2))
    assert capture.load_valid() == {}
    assert capture.is_complete() is False
    assert not capture.staging_dir.exists()


# ---------------------------------------------------------------------------
# Resume-only-missing
# ---------------------------------------------------------------------------
def test_resume_skips_all_valid_without_any_request(tmp_path: Path) -> None:
    specs = _specs(3)
    capture = _capture(tmp_path, specs)
    for spec in specs:
        capture.ingest_body(spec, _body(spec), retrieved_at="2020-01-01T00:00:00Z")

    def _must_not_request(endpoint: str, params: dict):
        raise AssertionError(f"resume re-requested a valid {endpoint!r}")

    assert capture.run(_must_not_request) == 0
    assert len(list(capture.staging_dir.iterdir())) == len(specs)


def test_resume_requests_only_missing_identities(tmp_path: Path) -> None:
    specs = _specs(5)
    capture = _capture(tmp_path, specs)
    for spec in specs[:2]:
        capture.ingest_body(spec, _body(spec))

    calls: list[str] = []
    assert capture.run(_transport(_all_ok(specs), calls=calls)) == 0
    assert calls == [spec.endpoint for spec in specs[2:]]


# ---------------------------------------------------------------------------
# Boundedness: one attempt, stop on first non-success, no sleep/retry
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_transient_stops_after_exactly_one_attempt(
    status: int, tmp_path: Path
) -> None:
    specs = _specs(3)
    capture = _capture(tmp_path, specs)
    calls: list[str] = []

    def transport(endpoint: str, params: dict):
        calls.append(endpoint)
        return (status, {"detail": "temporary"})

    assert capture.run(transport) == 3
    assert calls == [specs[0].endpoint]

    ledger = _read_ledger(capture)
    assert ledger["_provenance"]["status"] == "NON_FINAL_STAGING"
    assert ledger["_provenance"]["state"] == "INCOMPLETE"
    assert ledger["last_blocker"]["classification"] == "transient"
    assert ledger["last_blocker"]["status_code"] == status
    assert ledger["last_blocker"]["body"] == {"detail": "temporary"}
    assert not capture.canonical_dir.exists()


def test_unexpected_status_is_not_transient_and_stops(tmp_path: Path) -> None:
    specs = _specs(2)
    capture = _capture(tmp_path, specs)
    calls: list[str] = []

    def transport(endpoint: str, params: dict):
        calls.append(endpoint)
        return (403, {"detail": "forbidden"})

    assert capture.run(transport) == 3
    assert calls == [specs[0].endpoint]
    assert _read_ledger(capture)["last_blocker"]["classification"] == "unexpected"


def test_transport_exception_becomes_blocker_and_stops(tmp_path: Path) -> None:
    specs = _specs(2)
    capture = _capture(tmp_path, specs)
    calls: list[str] = []

    def transport(endpoint: str, params: dict):
        calls.append(endpoint)
        raise RuntimeError("connection reset by peer")

    assert capture.run(transport) == 3
    assert calls == [specs[0].endpoint]
    blocker = _read_ledger(capture)["last_blocker"]
    assert blocker["status_code"] == 0
    assert blocker["classification"] == "unexpected"
    assert "connection reset by peer" in blocker["error"]


def test_transient_preserves_prior_staged_successes(tmp_path: Path) -> None:
    specs = _specs(4)
    capture = _capture(tmp_path, specs)
    for spec in specs[:2]:
        capture.ingest_body(spec, _body(spec))

    blocked = specs[2]
    calls: list[str] = []

    def transport(endpoint: str, params: dict):
        calls.append(endpoint)
        return (503, {"detail": "upstream"})

    assert capture.run(transport) == 3
    assert calls == [blocked.endpoint]
    assert set(capture.load_valid()) == {spec.identity for spec in specs[:2]}
    assert not capture.staged_path(blocked.identity).exists()

    ledger = _read_ledger(capture)
    assert ledger["valid_captured_identities"] == [
        spec.identity for spec in specs[:2]
    ]
    assert ledger["remaining_identities"] == [
        spec.identity for spec in specs[2:]
    ]
    assert not capture.canonical_dir.exists()


def test_blocker_history_is_append_only_across_resumes(tmp_path: Path) -> None:
    specs = _specs(3)
    capture = _capture(tmp_path, specs)

    assert capture.run(lambda e, p: (429, {"detail": "first"})) == 3
    assert capture.run(lambda e, p: (500, {"detail": "second"})) == 3

    ledger = _read_ledger(capture)
    assert [b["status_code"] for b in ledger["blockers"]] == [429, 500]
    assert [b["body"]["detail"] for b in ledger["blockers"]] == [
        "first",
        "second",
    ]
    assert ledger["last_blocker"]["body"] == {"detail": "second"}


# ---------------------------------------------------------------------------
# Canonical-consistency: hash/conflict detection fails closed
# ---------------------------------------------------------------------------
def test_corrupted_staged_body_fails_closed(tmp_path: Path) -> None:
    specs = _specs(1)
    capture = _capture(tmp_path, specs)
    capture.ingest_body(specs[0], _body(specs[0]))
    path = capture.staged_path(specs[0].identity)
    record = json.loads(path.read_text(encoding="utf-8"))
    record["body"] = {"tampered": True}
    path.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(mod.StagedCaptureError):
        capture.load_valid()


def test_metadata_conflict_fails_closed(tmp_path: Path) -> None:
    specs = _specs(1)
    capture = _capture(tmp_path, specs)
    capture.ingest_body(specs[0], _body(specs[0]))
    path = capture.staged_path(specs[0].identity)
    record = json.loads(path.read_text(encoding="utf-8"))
    record["metadata"] = {"role": "changed"}
    path.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(mod.StagedCaptureError):
        capture.load_valid()


def test_request_mismatch_fails_closed(tmp_path: Path) -> None:
    specs = _specs(1)
    capture = _capture(tmp_path, specs)
    capture.ingest_body(specs[0], _body(specs[0]))
    path = capture.staged_path(specs[0].identity)
    record = json.loads(path.read_text(encoding="utf-8"))
    record["endpoint"] = "/wrong/endpoint"
    path.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(mod.StagedCaptureError):
        capture.load_valid()


def test_unexpected_staged_file_fails_closed(tmp_path: Path) -> None:
    capture = _capture(tmp_path, _specs(1))
    capture.staging_dir.mkdir(parents=True, exist_ok=True)
    (capture.staging_dir / "zzz_unexpected.json").write_text(
        "{}", encoding="utf-8"
    )
    with pytest.raises(mod.StagedCaptureError):
        capture.load_valid()


def test_ingest_detects_same_identity_conflict(tmp_path: Path) -> None:
    specs = _specs(1)
    capture = _capture(tmp_path, specs)
    first = capture.ingest_body(specs[0], {"value": 1})
    # Identical body is idempotent.
    again = capture.ingest_body(specs[0], {"value": 1})
    assert again["content_sha256"] == first["content_sha256"]
    # Different body under the same identity is a conflict, not an overwrite.
    with pytest.raises(mod.StagedCaptureError):
        capture.ingest_body(specs[0], {"value": 2})
    assert capture.load_valid()[specs[0].identity]["body"] == {"value": 1}


def test_ingest_rejects_foreign_identity(tmp_path: Path) -> None:
    specs = _specs(1)
    capture = _capture(tmp_path, specs)
    foreign = mod.RequestSpec.build("/api/other", {"z": 1})
    with pytest.raises(mod.StagedCaptureError):
        capture.ingest_body(foreign, {"value": 1})


# ---------------------------------------------------------------------------
# Ledger integrity
# ---------------------------------------------------------------------------
def test_ledger_with_wrong_status_is_refused(tmp_path: Path) -> None:
    capture = _capture(tmp_path, _specs(1))
    capture.ledger_path.write_text(
        json.dumps(
            {
                "_provenance": {"status": "FINAL"},
                "expected_identities": [],
                "blockers": [],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(mod.StagedCaptureError):
        capture.run(lambda e, p: (200, {"ok": True}))


def test_ledger_identity_mismatch_is_refused(tmp_path: Path) -> None:
    capture = _capture(tmp_path, _specs(1))
    capture.ledger_path.write_text(
        json.dumps(
            {
                "_provenance": {"status": "NON_FINAL_STAGING"},
                "expected_identities": [{"identity": "not-this-set"}],
                "blockers": [],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(mod.StagedCaptureError):
        capture.run(lambda e, p: (200, {"ok": True}))


# ---------------------------------------------------------------------------
# Atomic promote-only-when-complete
# ---------------------------------------------------------------------------
def test_incomplete_staging_cannot_promote(tmp_path: Path) -> None:
    specs = _specs(3)
    capture = _capture(tmp_path, specs)
    capture.ingest_body(specs[0], _body(specs[0]))
    with pytest.raises(mod.StagedCaptureError):
        capture.promote()
    assert not capture.canonical_dir.exists()


def test_exact_complete_set_required_for_promotion(tmp_path: Path) -> None:
    specs = _specs(2)
    capture = _capture(tmp_path, specs)
    for spec in specs:
        capture.ingest_body(spec, _body(spec))
    valid = capture.load_valid()

    incomplete = dict(valid)
    del incomplete[specs[0].identity]
    with pytest.raises(mod.StagedCaptureError):
        capture._validate_complete(incomplete)

    extra = dict(valid)
    extra["extra"] = valid[specs[0].identity]
    with pytest.raises(mod.StagedCaptureError):
        capture._validate_complete(extra)


def test_promotion_writes_complete_canonical_set_and_manifest(
    tmp_path: Path,
) -> None:
    specs = _specs(3)
    capture = _capture(tmp_path, specs)
    assert capture.run(_transport(_all_ok(specs))) == 0
    capture.promote(
        build_manifest=lambda valid: {
            "count": len(valid),
            "identities": sorted(valid),
        }
    )

    for spec in specs:
        promoted = json.loads(
            (capture.canonical_dir / spec.resolved_canonical_name).read_text(
                encoding="utf-8"
            )
        )
        assert promoted == _body(spec)
    manifest = json.loads(
        (capture.canonical_dir / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["count"] == len(specs)
    parent = capture.canonical_dir.parent
    assert not (parent / (capture.canonical_dir.name + ".promote-tmp")).exists()
    assert not (
        parent / (capture.canonical_dir.name + ".promote-backup")
    ).exists()
    # Staging remains as non-final evidence after promotion.
    assert capture.staging_dir.is_dir()


def test_staging_is_not_consumable_as_canonical(tmp_path: Path) -> None:
    specs = _specs(2)
    capture = _capture(tmp_path, specs)
    assert capture.run(_transport(_all_ok(specs))) == 0
    # A complete staging area is not a canonical set until promotion.
    assert not capture.canonical_dir.exists()


def test_promotion_failure_keeps_previous_canonical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    specs = _specs(2)
    capture = _capture(tmp_path, specs)
    for spec in specs:
        capture.ingest_body(spec, _body(spec))

    capture.canonical_dir.mkdir(parents=True)
    (capture.canonical_dir / "manifest.json").write_text(
        json.dumps({"old": True}), encoding="utf-8"
    )
    (capture.canonical_dir / "old.json").write_text(
        json.dumps({"_old": True}), encoding="utf-8"
    )

    real_write = mod._write_json

    def _boom(path: Path, obj: object) -> None:
        if path.name == "manifest.json":
            raise RuntimeError("simulated promotion failure")
        real_write(path, obj)

    monkeypatch.setattr(mod, "_write_json", _boom)
    with pytest.raises(RuntimeError):
        capture.promote(build_manifest=lambda valid: {"new": True})

    unchanged = json.loads(
        (capture.canonical_dir / "manifest.json").read_text(encoding="utf-8")
    )
    assert unchanged == {"old": True}
    assert (capture.canonical_dir / "old.json").is_file()
    parent = capture.canonical_dir.parent
    assert not (parent / (capture.canonical_dir.name + ".promote-tmp")).exists()
    assert not (
        parent / (capture.canonical_dir.name + ".promote-backup")
    ).exists()


def test_promotion_guard_can_block(tmp_path: Path) -> None:
    specs = _specs(2)
    seen: list[set] = []

    def guard(valid: dict) -> None:
        seen.append(set(valid))
        raise mod.StagedCaptureError("guard blocked promotion")

    capture = _capture(tmp_path, specs, promotion_guard=guard)
    for spec in specs:
        capture.ingest_body(spec, _body(spec))
    with pytest.raises(mod.StagedCaptureError):
        capture.promote()
    assert seen == [{spec.identity for spec in specs}]
    assert not capture.canonical_dir.exists()


def test_promote_without_canonical_dir_fails_closed(tmp_path: Path) -> None:
    specs = _specs(1)
    capture = mod.StagedCaptureSet(
        specs,
        staging_dir=tmp_path / "staging",
        ledger_path=tmp_path / "ledger.json",
    )
    capture.ingest_body(specs[0], _body(specs[0]))
    with pytest.raises(mod.StagedCaptureError):
        capture.promote()


# ---------------------------------------------------------------------------
# Source-level guard: no network client, no sleep, no runnable CLI
# ---------------------------------------------------------------------------
def test_module_imports_no_network_client_and_never_sleeps() -> None:
    source = _MODULE_PATH.read_text(encoding="utf-8")
    assert "import time" not in source
    assert "time.sleep" not in source
    assert "sleep(" not in source
    assert "urllib" not in source
    assert "socket" not in source
    assert "requests" not in source


def test_module_is_a_library_not_a_network_cli() -> None:
    with pytest.raises(SystemExit):
        mod.main()
