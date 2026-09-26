"""D2 tests — resumable Tiingo EOD acquisition and manifest.

Frozen spec: ``worker_tasks/dataset_v1/task-d2-acquisition.md`` under
``worker_tasks/dataset_v1/dataset-v1-wave1-plan.md`` and the frozen contract
``docs/discovery_dataset_v1_contract.md``.

Every test is offline: transports are fakes, the clock is injected, sleep is
a no-op, storage uses ``tmp_path`` and ``socket.socket.connect`` is blocked
process-wide for this module. No credential value is read, printed or
persisted.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import socket
import sys
import urllib.request
from pathlib import Path

import pytest

from smart_beta.dataset_v1 import acquisition as acq

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "dataset_v1" / "d2"
POOL_FIXTURE = FIXTURE_DIR / "expected_pool.json"
CONTRACT = acq.CONTRACT_SHA256
FIXED_CLOCK = "2024-06-03T00:00:00Z"
TICKERS = ["AAPL", "MSFT", "NVDA"]


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _no_sockets(monkeypatch: pytest.MonkeyPatch):
    """Adversarial guard: no test may open a socket."""

    def _refuse(*args: object, **kwargs: object) -> None:
        raise RuntimeError("D2 tests must never open a socket")

    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(socket, "create_connection", _refuse)
    yield


def fixed_clock(value: str = FIXED_CLOCK):
    return lambda: value


class SleepSpy:
    def __init__(self) -> None:
        self.calls: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


def ticker_of(path: str) -> str:
    return path.split("/")[-2]


class FakeTransport:
    """Deterministic per-ticker fake transport.

    ``handler(ticker, attempt_number, path, params)`` returns
    ``(status, bytes)`` or raises.
    """

    def __init__(self, handler):
        self.handler = handler
        self.calls: list[dict] = []
        self.counts: dict[str, int] = {}

    def __call__(self, path, params, timeout_s):
        ticker = ticker_of(path)
        self.counts[ticker] = self.counts.get(ticker, 0) + 1
        self.calls.append(
            {
                "path": path,
                "params": dict(params),
                "timeout_s": timeout_s,
                "ticker": ticker,
            }
        )
        return self.handler(ticker, self.counts[ticker], path, params)


def eod_rows(n: int = 2) -> list[dict]:
    return [
        {
            "date": f"2024-01-{i + 2:02d}",
            "close": 100.0 + i,
            "volume": 1000 + i,
            "divCash": 0.0,
            "splitFactor": 1.0,
        }
        for i in range(n)
    ]


def ok_body(n: int = 2) -> bytes:
    return json.dumps(eod_rows(n)).encode("utf-8")


EMPTY_BODY = b"[]"


def pool_bytes(pool: dict) -> bytes:
    return (acq.canonical_json(pool) + "\n").encode("utf-8")


def load_pool() -> dict:
    return json.loads(POOL_FIXTURE.read_text("utf-8"))


def write_pool(tmp_path: Path, pool: dict) -> Path:
    path = tmp_path / "pool.json"
    path.write_bytes(pool_bytes(pool))
    return path


def make_batch(tmp_path: Path, clock: str = FIXED_CLOCK, **kwargs) -> str:
    return acq.create_batch(POOL_FIXTURE, tmp_path, fixed_clock(clock), **kwargs)


def read_status(root: Path, batch_id: str) -> dict:
    return json.loads(
        (root / "manifest" / batch_id / "status.json").read_text("utf-8")
    )


def read_header(root: Path, batch_id: str) -> dict:
    return json.loads(
        (root / "manifest" / batch_id / "batch.json").read_text("utf-8")
    )


def read_attempts(root: Path, batch_id: str) -> list[dict]:
    path = root / "manifest" / batch_id / "attempts.jsonl"
    return [json.loads(line) for line in path.read_text("utf-8").splitlines() if line]


def raw_files(root: Path, batch_id: str) -> list[str]:
    return sorted(p.name for p in (root / "raw" / batch_id).iterdir())


# ---------------------------------------------------------------------------
# Expected-pool verification (task §1)
# ---------------------------------------------------------------------------
def test_create_batch_returns_batch_id_and_header(tmp_path: Path) -> None:
    batch_id = make_batch(tmp_path)
    assert batch_id.startswith("b-")
    assert len(batch_id) == 18
    header = read_header(tmp_path, batch_id)
    assert header["batch_id"] == batch_id
    assert header["contract_sha256"] == CONTRACT
    assert header["manifest_schema_version"] == acq.MANIFEST_SCHEMA_VERSION
    assert header["acquisition_start"] == "2015-01-02"
    assert header["C"] == "2024-05-31"
    assert len(header["acquisition_tool_sha256"]) == 64
    assert len(header["adapter_code_sha256"]) == 64
    assert [e["ticker"] for e in header["tickers"]] == TICKERS
    assert header["tickers"][0]["security_keys"] == ["TIINGO:AAPL:1980-12-12"]


def test_adapter_hash_is_the_client_file_hash(tmp_path: Path) -> None:
    from smart_beta.vendors.tiingo import client as tiingo_client

    batch_id = make_batch(tmp_path)
    header = read_header(tmp_path, batch_id)
    assert header["adapter_code_sha256"] == hashlib.sha256(
        Path(tiingo_client.__file__).read_bytes()
    ).hexdigest()


def test_wrong_schema_fails(tmp_path: Path) -> None:
    pool = load_pool()
    pool["schema"] = "discovery-dataset-v1-expected-pool-v2"
    with pytest.raises(acq.ExpectedPoolError):
        acq.create_batch(write_pool(tmp_path, pool), tmp_path, fixed_clock())


def test_wrong_contract_hash_fails(tmp_path: Path) -> None:
    pool = load_pool()
    pool["contract_sha256"] = "0" * 64
    with pytest.raises(acq.ExpectedPoolError):
        acq.create_batch(write_pool(tmp_path, pool), tmp_path, fixed_clock())


def test_file_hash_mismatch_fails(tmp_path: Path) -> None:
    with pytest.raises(acq.ExpectedPoolError):
        acq.create_batch(
            POOL_FIXTURE,
            tmp_path,
            fixed_clock(),
            expected_pool_sha256="0" * 64,
        )


def test_non_canonical_pool_bytes_fail(tmp_path: Path) -> None:
    parsed = load_pool()
    non_canonical = (json.dumps(parsed, indent=2) + "\n").encode("utf-8")
    path = tmp_path / "pool.json"
    path.write_bytes(non_canonical)
    with pytest.raises(acq.ExpectedPoolError):
        acq.create_batch(path, tmp_path, fixed_clock())


def test_invalid_ticker_in_pool_fails_before_path(tmp_path: Path) -> None:
    pool = load_pool()
    pool["tickers"][0]["ticker"] = "bad ticker"
    with pytest.raises(acq.TickerValidationError):
        acq.create_batch(write_pool(tmp_path, pool), tmp_path, fixed_clock())
    assert not (tmp_path / "raw").exists()


def test_does_not_import_d1() -> None:
    assert "smart_beta.dataset_v1.candidate_pool" not in sys.modules


# ---------------------------------------------------------------------------
# Request construction (task §2)
# ---------------------------------------------------------------------------
def test_request_path_and_params(tmp_path: Path) -> None:
    batch_id = make_batch(tmp_path)
    transport = FakeTransport(lambda t, n, p, q: (200, ok_body()))
    acq.run_batch(batch_id, tmp_path, transport, SleepSpy(), fixed_clock())

    assert transport.calls[0]["path"] == "/tiingo/daily/AAPL/prices"
    assert transport.calls[0]["params"] == {
        "startDate": "2015-01-02",
        "endDate": "2024-05-31",
    }
    assert transport.calls[0]["timeout_s"] == acq.DEFAULT_TIMEOUT_S
    assert {c["path"] for c in transport.calls} == {
        f"/tiingo/daily/{ticker}/prices" for ticker in TICKERS
    }


def test_request_path_quotes_ticker(tmp_path: Path) -> None:
    assert acq.eod_request_path("BRK.B") == "/tiingo/daily/BRK.B/prices"
    assert acq.eod_request_path("A B") == "/tiingo/daily/A%20B/prices"


def test_live_transport_uses_header_and_never_url_key(monkeypatch) -> None:
    sentinel = "d2-sentinel-secret-key"
    monkeypatch.setenv("TIINGO_API_KEY", sentinel)
    captured: dict = {}

    class _Response:
        status = 200

        def read(self) -> bytes:
            return EMPTY_BODY

        def __enter__(self):
            return self

        def __exit__(self, *exc) -> bool:
            return False

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["headers"] = dict(request.header_items())
        captured["timeout"] = timeout
        return _Response()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    transport = acq.make_live_transport()
    status, raw = transport(
        "/tiingo/daily/AAPL/prices",
        {"startDate": "2015-01-02", "endDate": "2024-05-31"},
        12.0,
    )

    assert status == 200
    assert raw == EMPTY_BODY
    assert captured["headers"]["Authorization"] == f"Token {sentinel}"
    assert sentinel not in captured["url"]
    assert "Authorization" in captured["headers"]
    assert captured["timeout"] == 12.0


def test_live_transport_missing_key_fails_closed(monkeypatch) -> None:
    monkeypatch.delenv("TIINGO_API_KEY", raising=False)
    transport = acq.make_live_transport()
    with pytest.raises(acq.ConfigError):
        transport("/tiingo/daily/AAPL/prices", {}, 1.0)


# ---------------------------------------------------------------------------
# Raw storage (task §3)
# ---------------------------------------------------------------------------
def test_exact_received_bytes_are_stored(tmp_path: Path) -> None:
    batch_id = make_batch(tmp_path)
    body = ok_body(3)
    transport = FakeTransport(lambda t, n, p, q: (200, body))
    acq.run_batch(batch_id, tmp_path, transport, SleepSpy(), fixed_clock())

    stored = (tmp_path / "raw" / batch_id / "AAPL.json.gz").read_bytes()
    assert gzip.decompress(stored) == body


def test_gzip_is_deterministic_and_filename_free() -> None:
    first = acq.gzip_bytes(b"exact bytes")
    second = acq.gzip_bytes(b"exact bytes")
    assert first == second
    assert first[3] == 0  # FLG: FNAME bit clear
    assert first[4:8] == b"\x00\x00\x00\x00"  # MTIME == 0


def test_stored_gzip_is_deterministic(tmp_path: Path) -> None:
    batch_id = make_batch(tmp_path)
    body = ok_body()
    transport = FakeTransport(lambda t, n, p, q: (200, body))
    acq.run_batch(batch_id, tmp_path, transport, SleepSpy(), fixed_clock())
    stored = (tmp_path / "raw" / batch_id / "AAPL.json.gz").read_bytes()
    assert stored == acq.gzip_bytes(body)


@pytest.mark.parametrize("bad", ["bad ticker", "aapl", "AAPL/EVIL", "-AAPL", "", "AAPL$"])
def test_invalid_ticker_rejected_without_constructing_path(bad: str) -> None:
    with pytest.raises(acq.TickerValidationError):
        acq.validate_ticker(bad)


def test_invalid_ticker_store_makes_no_path(tmp_path: Path) -> None:
    with pytest.raises(acq.TickerValidationError):
        acq._store_raw(tmp_path, "b-0000000000000000", "bad ticker", EMPTY_BODY)
    assert not (tmp_path / "raw").exists()


def test_raw_file_is_never_overwritten(tmp_path: Path) -> None:
    batch_id = make_batch(tmp_path)
    first = FakeTransport(lambda t, n, p, q: (200, ok_body(1)))
    acq.run_batch(batch_id, tmp_path, first, SleepSpy(), fixed_clock())
    original = (tmp_path / "raw" / batch_id / "AAPL.json.gz").read_bytes()

    # Refetch mode with a different body must keep the original untouched.
    second = FakeTransport(lambda t, n, p, q: (200, ok_body(4)))
    acq.refetch(
        batch_id,
        tmp_path,
        second,
        SleepSpy(),
        fixed_clock(),
        tickers=["AAPL"],
    )
    assert (tmp_path / "raw" / batch_id / "AAPL.json.gz").read_bytes() == original
    assert set(raw_files(tmp_path, batch_id)) == {
        "AAPL.json.gz",
        "AAPL.refetch-1.json.gz",
        "MSFT.json.gz",
        "NVDA.json.gz",
    }


# ---------------------------------------------------------------------------
# Response classification (task §4)
# ---------------------------------------------------------------------------
def test_classify_ok_records_rows_and_dates() -> None:
    result = acq.classify_body(ok_body(3))
    assert result.status == "OK"
    assert result.row_count == 3
    assert result.first_date == "2024-01-02"
    assert result.last_date == "2024-01-04"


def test_classify_empty() -> None:
    result = acq.classify_body(EMPTY_BODY)
    assert result.status == "EMPTY"
    assert result.row_count == 0


@pytest.mark.parametrize(
    "raw",
    [
        b'{"not": "a list"}',
        b"[1, 2, 3]",
        b"null",
    ],
)
def test_classify_non_list_is_malformed(raw: bytes) -> None:
    assert acq.classify_body(raw).status == "MALFORMED"


def test_classify_missing_key_is_malformed() -> None:
    body = json.dumps(
        [{"date": "2024-01-02", "close": 1.0, "volume": 1, "divCash": 0.0}]
    ).encode("utf-8")
    assert acq.classify_body(body).status == "MALFORMED"


def test_classify_truncated_is_malformed() -> None:
    partial = b'[{"date":"2024-01-02","close":1.0,"volume":1,"divCash":0.0,"splitFactor":1.0'
    assert acq.classify_body(partial).status == "MALFORMED"


def test_classify_empty_bytes_is_malformed() -> None:
    assert acq.classify_body(b"").status == "MALFORMED"


def test_partial_body_status_200_cannot_be_ok(tmp_path: Path) -> None:
    batch_id = make_batch(tmp_path)
    partial = b'[{"date":"2024-01-02","close":1.0,"volume":1,"divCash":0.0,"splitFactor":1.0'
    transport = FakeTransport(lambda t, n, p, q: (200, partial))
    summary = acq.run_batch(batch_id, tmp_path, transport, SleepSpy(), fixed_clock())
    assert summary["tickers"]["AAPL"]["status"] == "MALFORMED"
    assert "OK" not in summary["counts"]


# ---------------------------------------------------------------------------
# Failure handling (task §5, contract §15)
# ---------------------------------------------------------------------------
def test_5xx_then_success(tmp_path: Path) -> None:
    batch_id = make_batch(tmp_path)

    def handler(ticker: str, n: int, path: str, params: dict):
        if ticker == "AAPL":
            return (500, b"server error") if n == 1 else (200, ok_body())
        return (200, ok_body())

    sleep = SleepSpy()
    transport = FakeTransport(handler)
    summary = acq.run_batch(batch_id, tmp_path, transport, sleep, fixed_clock())

    assert summary["batch_state"] == "COMPLETE"
    assert summary["tickers"]["AAPL"]["status"] == "OK"
    assert transport.counts["AAPL"] == 2
    assert sleep.calls  # backoff slept once


def test_5xx_three_times_retry_exhausted(tmp_path: Path) -> None:
    batch_id = make_batch(tmp_path)

    def handler(ticker: str, n: int, path: str, params: dict):
        if ticker == "AAPL":
            return (503, b"unavailable")
        return (200, ok_body())

    sleep = SleepSpy()
    transport = FakeTransport(handler)
    summary = acq.run_batch(batch_id, tmp_path, transport, sleep, fixed_clock())

    assert summary["tickers"]["AAPL"]["status"] == "RETRY_EXHAUSTED"
    assert transport.counts["AAPL"] == acq.MAX_ATTEMPTS == 3
    assert len(sleep.calls) == 2


def test_timeout_three_times_retry_exhausted(tmp_path: Path) -> None:
    batch_id = make_batch(tmp_path)

    def handler(ticker: str, n: int, path: str, params: dict):
        if ticker == "AAPL":
            raise TimeoutError("timed out")
        return (200, ok_body())

    transport = FakeTransport(handler)
    summary = acq.run_batch(batch_id, tmp_path, transport, SleepSpy(), fixed_clock())

    assert summary["tickers"]["AAPL"]["status"] == "RETRY_EXHAUSTED"
    assert transport.counts["AAPL"] == 3


def test_quota_exhausted_leaves_pending_then_resume_completes(tmp_path: Path) -> None:
    batch_id = make_batch(tmp_path)
    quota = {"on": True}

    def handler(ticker: str, n: int, path: str, params: dict):
        if quota["on"]:
            return (429, b"rate limited")
        return (200, ok_body())

    first = FakeTransport(handler)
    summary = acq.run_batch(batch_id, tmp_path, first, SleepSpy(), fixed_clock())
    assert summary["batch_state"] == "QUOTA_EXHAUSTED"
    assert summary["tickers"]["AAPL"]["status"] == "PENDING"
    assert first.counts["AAPL"] == 3

    quota["on"] = False
    second = FakeTransport(handler)
    resumed = acq.run_batch(batch_id, tmp_path, second, SleepSpy(), fixed_clock())
    assert resumed["batch_state"] == "COMPLETE"
    assert all(
        record["status"] == "OK" for record in resumed["tickers"].values()
    )
    assert second.counts["AAPL"] == 1


def test_entitlement_error_has_single_attempt(tmp_path: Path) -> None:
    batch_id = make_batch(tmp_path)

    def handler(ticker: str, n: int, path: str, params: dict):
        if ticker == "AAPL":
            return (403, b"forbidden")
        return (200, ok_body())

    transport = FakeTransport(handler)
    summary = acq.run_batch(batch_id, tmp_path, transport, SleepSpy(), fixed_clock())

    assert summary["tickers"]["AAPL"]["status"] == "ENTITLEMENT_ERROR"
    assert transport.counts["AAPL"] == 1


def test_interrupted_run_then_resume(tmp_path: Path) -> None:
    batch_id = make_batch(tmp_path)

    def handler(ticker: str, n: int, path: str, params: dict):
        if ticker == "MSFT":
            raise RuntimeError("interrupted mid-batch")
        return (200, ok_body())

    with pytest.raises(RuntimeError):
        acq.run_batch(batch_id, tmp_path, FakeTransport(handler), SleepSpy(), fixed_clock())

    status = read_status(tmp_path, batch_id)
    assert status["batch_state"] == "INTERRUPTED"
    assert status["tickers"]["AAPL"]["status"] == "OK"
    assert status["tickers"]["MSFT"]["status"] == "PENDING"
    assert status["tickers"]["NVDA"]["status"] == "PENDING"

    resumed_transport = FakeTransport(lambda t, n, p, q: (200, ok_body()))
    resumed = acq.run_batch(
        batch_id, tmp_path, resumed_transport, SleepSpy(), fixed_clock()
    )
    assert resumed["batch_state"] == "COMPLETE"
    assert set(resumed["tickers"]) == set(TICKERS)
    assert set(resumed_transport.counts) == {"MSFT", "NVDA"}  # AAPL skipped
    assert raw_files(tmp_path, batch_id) == [
        "AAPL.json.gz",
        "MSFT.json.gz",
        "NVDA.json.gz",
    ]


# ---------------------------------------------------------------------------
# Resume and idempotence (task §6)
# ---------------------------------------------------------------------------
def test_completed_rerun_makes_zero_transport_calls(tmp_path: Path) -> None:
    batch_id = make_batch(tmp_path)
    first = FakeTransport(lambda t, n, p, q: (200, ok_body()))
    acq.run_batch(batch_id, tmp_path, first, SleepSpy(), fixed_clock())

    def _explode(*args):
        raise AssertionError("a completed batch must not issue requests")

    second = FakeTransport(_explode)
    summary = acq.run_batch(batch_id, tmp_path, second, SleepSpy(), fixed_clock())
    assert second.calls == []
    assert summary["requests_made"] == 0
    assert summary["batch_state"] == "COMPLETE"


def test_resume_skips_verified_ok_ticker(tmp_path: Path) -> None:
    batch_id = make_batch(tmp_path)
    first = FakeTransport(lambda t, n, p, q: (200, ok_body()))
    acq.run_batch(batch_id, tmp_path, first, SleepSpy(), fixed_clock())
    # Corrupt nothing: a plain resume must skip every verified OK file.
    second = FakeTransport(lambda t, n, p, q: (200, ok_body(9)))
    acq.run_batch(batch_id, tmp_path, second, SleepSpy(), fixed_clock())
    assert second.calls == []


def test_corrupt_local_file_becomes_pending_and_refetches(tmp_path: Path) -> None:
    batch_id = make_batch(tmp_path)
    first = FakeTransport(lambda t, n, p, q: (200, ok_body()))
    acq.run_batch(batch_id, tmp_path, first, SleepSpy(), fixed_clock())

    corrupted = tmp_path / "raw" / batch_id / "AAPL.json.gz"
    corrupted.write_bytes(acq.gzip_bytes(b"corrupted content"))

    second = FakeTransport(lambda t, n, p, q: (200, ok_body()))
    summary = acq.run_batch(batch_id, tmp_path, second, SleepSpy(), fixed_clock())

    assert second.counts["AAPL"] == 1
    assert summary["tickers"]["AAPL"]["status"] == "OK"
    events = [a for a in read_attempts(tmp_path, batch_id) if a["error_class"] == "CORRUPT_LOCAL"]
    assert len(events) == 1
    assert events[0]["ticker"] == "AAPL"


# ---------------------------------------------------------------------------
# Refetch / NONDETERMINISTIC (task §7)
# ---------------------------------------------------------------------------
def test_corrupt_non_gzip_file_becomes_pending(tmp_path: Path) -> None:
    batch_id = make_batch(tmp_path)
    first = FakeTransport(lambda t, n, p, q: (200, ok_body()))
    acq.run_batch(batch_id, tmp_path, first, SleepSpy(), fixed_clock())

    (tmp_path / "raw" / batch_id / "AAPL.json.gz").write_bytes(b"not a gzip stream")

    second = FakeTransport(lambda t, n, p, q: (200, ok_body()))
    summary = acq.run_batch(batch_id, tmp_path, second, SleepSpy(), fixed_clock())
    assert second.counts["AAPL"] == 1
    assert summary["tickers"]["AAPL"]["status"] == "OK"
    assert any(
        a["error_class"] == "CORRUPT_LOCAL"
        for a in read_attempts(tmp_path, batch_id)
    )


def test_refetch_different_body_is_nondeterministic_and_keeps_both(tmp_path: Path) -> None:
    batch_id = make_batch(tmp_path)
    first = FakeTransport(lambda t, n, p, q: (200, ok_body(2)))
    acq.run_batch(batch_id, tmp_path, first, SleepSpy(), fixed_clock())

    second = FakeTransport(lambda t, n, p, q: (200, ok_body(5)))
    summary = acq.refetch(
        batch_id, tmp_path, second, SleepSpy(), fixed_clock(), tickers=["AAPL"]
    )

    assert summary["tickers"]["AAPL"]["status"] == "NONDETERMINISTIC"
    assert set(second.counts) == {"AAPL"}
    assert sorted(
        name for name in raw_files(tmp_path, batch_id) if name.startswith("AAPL")
    ) == ["AAPL.json.gz", "AAPL.refetch-1.json.gz"]


def test_refetch_same_body_keeps_ok(tmp_path: Path) -> None:
    batch_id = make_batch(tmp_path)
    body = ok_body(2)
    first = FakeTransport(lambda t, n, p, q: (200, body))
    acq.run_batch(batch_id, tmp_path, first, SleepSpy(), fixed_clock())

    second = FakeTransport(lambda t, n, p, q: (200, body))
    summary = acq.refetch(
        batch_id, tmp_path, second, SleepSpy(), fixed_clock(), tickers=["AAPL"]
    )

    assert summary["tickers"]["AAPL"]["status"] == "OK"
    assert "AAPL.refetch-1.json.gz" not in raw_files(tmp_path, batch_id)


def test_normal_run_never_refetches_ok(tmp_path: Path) -> None:
    batch_id = make_batch(tmp_path)
    first = FakeTransport(lambda t, n, p, q: (200, ok_body()))
    acq.run_batch(batch_id, tmp_path, first, SleepSpy(), fixed_clock())
    second = FakeTransport(lambda t, n, p, q: (200, ok_body(9)))
    acq.run_batch(batch_id, tmp_path, second, SleepSpy(), fixed_clock())
    assert second.calls == []


# ---------------------------------------------------------------------------
# Manifest determinism (task §9)
# ---------------------------------------------------------------------------
def test_batch_id_is_stable_across_resume(tmp_path: Path) -> None:
    batch_id = make_batch(tmp_path)
    assert make_batch(tmp_path) == batch_id
    first = FakeTransport(lambda t, n, p, q: (200, ok_body()))
    acq.run_batch(batch_id, tmp_path, first, SleepSpy(), fixed_clock())
    assert read_status(tmp_path, batch_id)["batch_id"] == batch_id


def test_create_batch_is_idempotent_and_keeps_progress(tmp_path: Path) -> None:
    batch_id = make_batch(tmp_path)
    transport = FakeTransport(lambda t, n, p, q: (200, ok_body()))
    acq.run_batch(batch_id, tmp_path, transport, SleepSpy(), fixed_clock())
    assert make_batch(tmp_path) == batch_id
    status = read_status(tmp_path, batch_id)
    assert status["tickers"]["AAPL"]["status"] == "OK"


def test_manifest_is_deterministic(tmp_path: Path) -> None:
    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    batch_a = acq.create_batch(POOL_FIXTURE, root_a, fixed_clock())
    batch_b = acq.create_batch(POOL_FIXTURE, root_b, fixed_clock())
    assert batch_a == batch_b

    transport_a = FakeTransport(lambda t, n, p, q: (200, ok_body(2)))
    transport_b = FakeTransport(lambda t, n, p, q: (200, ok_body(2)))
    summary_a = acq.run_batch(batch_a, root_a, transport_a, SleepSpy(), fixed_clock())
    summary_b = acq.run_batch(batch_b, root_b, transport_b, SleepSpy(), fixed_clock())

    status_a = (root_a / "manifest" / batch_a / "status.json").read_bytes()
    status_b = (root_b / "manifest" / batch_b / "status.json").read_bytes()
    assert status_a == status_b
    assert hashlib.sha256(status_a).hexdigest() == hashlib.sha256(status_b).hexdigest()
    assert summary_a["manifest_sha256"] == summary_b["manifest_sha256"]
    assert summary_a["manifest_sha256"] == hashlib.sha256(status_a).hexdigest()


def test_status_is_sorted_and_terminals_are_visible(tmp_path: Path) -> None:
    batch_id = make_batch(tmp_path)
    first = FakeTransport(lambda t, n, p, q: (200, ok_body()))
    first_status = acq.run_batch(
        batch_id, tmp_path, first, SleepSpy(), fixed_clock()
    )
    assert [e["ticker"] for e in read_header(tmp_path, batch_id)["tickers"]] == TICKERS
    assert first_status["batch_state"] == "COMPLETE"
    for ticker in TICKERS:
        assert first_status["tickers"][ticker]["status"] == "OK"


def test_attempt_log_records_required_fields(tmp_path: Path) -> None:
    batch_id = make_batch(tmp_path)
    transport = FakeTransport(lambda t, n, p, q: (200, ok_body(2)))
    acq.run_batch(batch_id, tmp_path, transport, SleepSpy(), fixed_clock())
    attempts = read_attempts(tmp_path, batch_id)
    assert len(attempts) == len(TICKERS)
    record = attempts[0]
    for key in (
        "ticker",
        "security_keys",
        "endpoint",
        "requested_range",
        "retrieved_at_utc",
        "http_status",
        "attempt",
        "raw_sha256",
        "row_count",
        "first_date",
        "last_date",
    ):
        assert key in record, key
    assert record["http_status"] == 200
    assert record["endpoint"].endswith("/prices")
    assert record["requested_range"] == {
        "startDate": "2015-01-02",
        "endDate": "2024-05-31",
    }


# ---------------------------------------------------------------------------
# Bounded runs (task §11)
# ---------------------------------------------------------------------------
def test_max_requests_stops_cleanly(tmp_path: Path) -> None:
    batch_id = make_batch(tmp_path)
    transport = FakeTransport(lambda t, n, p, q: (200, ok_body()))
    summary = acq.run_batch(
        batch_id, tmp_path, transport, SleepSpy(), fixed_clock(), max_requests=1
    )
    assert summary["requests_made"] == 1
    assert len(transport.calls) == 1
    assert summary["batch_state"] == "INTERRUPTED"
    statuses = sorted(r["status"] for r in summary["tickers"].values())
    assert statuses == ["OK", "PENDING", "PENDING"]


def test_max_requests_resume_then_completes(tmp_path: Path) -> None:
    batch_id = make_batch(tmp_path)
    first = FakeTransport(lambda t, n, p, q: (200, ok_body()))
    acq.run_batch(batch_id, tmp_path, first, SleepSpy(), fixed_clock(), max_requests=2)
    second = FakeTransport(lambda t, n, p, q: (200, ok_body()))
    summary = acq.run_batch(batch_id, tmp_path, second, SleepSpy(), fixed_clock())
    assert summary["batch_state"] == "COMPLETE"
    assert len(second.calls) == 1


# ---------------------------------------------------------------------------
# Adversarial: credential containment
# ---------------------------------------------------------------------------
def test_sentinel_never_reaches_files_or_output(tmp_path, monkeypatch, capsys) -> None:
    sentinel = "d2-sentinel-credential-value-xyz"
    monkeypatch.setenv("TIINGO_API_KEY", sentinel)

    class _Response:
        status = 200

        def read(self) -> bytes:
            return ok_body()

        def __enter__(self):
            return self

        def __exit__(self, *exc) -> bool:
            return False

    monkeypatch.setattr(urllib.request, "urlopen", lambda request, timeout=None: _Response())

    batch_id = make_batch(tmp_path)
    transport = acq.make_live_transport()
    acq.run_batch(batch_id, tmp_path, transport, SleepSpy(), fixed_clock())

    for path in tmp_path.rglob("*"):
        if path.is_file():
            assert sentinel.encode("utf-8") not in path.read_bytes(), path

    captured = capsys.readouterr()
    assert sentinel not in captured.out
    assert sentinel not in captured.err


# ---------------------------------------------------------------------------
# CLI fail-closed behaviour (task §11)
# ---------------------------------------------------------------------------
def test_cli_requires_live_and_max_requests(tmp_path, capsys) -> None:
    batch_id = make_batch(tmp_path)
    rc = acq.main(["run", "--batch-id", batch_id, "--root", str(tmp_path)])
    assert rc == 2
    err = capsys.readouterr().err
    assert "--live" in err


def test_cli_live_requires_max_requests(tmp_path, capsys) -> None:
    batch_id = make_batch(tmp_path)
    rc = acq.main(
        ["--live", "run", "--batch-id", batch_id, "--root", str(tmp_path)]
    )
    assert rc == 2
    assert "--max-requests" in capsys.readouterr().err


def test_cli_create_is_offline(tmp_path, capsys) -> None:
    rc = acq.main(["create", "--pool", str(POOL_FIXTURE), "--root", str(tmp_path)])
    assert rc == 0
    assert capsys.readouterr().out.strip().startswith("b-")
