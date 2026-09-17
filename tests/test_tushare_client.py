"""Tests for the Tushare proxy transport (Phase 4D-B, P4DB-1).

Every test here is offline. ``fetch`` is exercised through an injected
:func:`replay_transport` over the specimens in
``tests/fixtures/tushare/client/`` (or an inline spy). An autouse fixture
additionally replaces ``urllib.request.urlopen`` with a tripwire so an
accidental live call fails the offending test loudly. Only
:class:`ProxyTushareClient`'s own live transport is exercised with a fake
``urlopen`` (to prove auth/status handling); no test reaches
``pcd.mobcvb.cn``.

Fixture provenance: ``TUSHARE_PROXY_TOKEN`` was unavailable when this task was
implemented, so the fixture bodies are *modeled* on the wire contract frozen
in the Phase 4D-B plan (see ``manifest.json``'s ``_provenance``), not live
captures. Tests assert the transport behaves correctly against that contract;
they do not claim to be a live certification run.
"""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from smart_beta.vendors.tushare.client import (
    TushareAPIError,
    TushareClient,
    TushareEmptyResponseError,
    TushareNonDeterministicResponseError,
)
from smart_beta.vendors.tushare.proxy_client import (
    TOKEN_ENV_VAR,
    ProxyTushareClient,
    check_canonical_consistency,
    fixture_key,
    record_fixture,
    replay_transport,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "tushare" / "client"
MANIFEST_PATH = FIXTURE_DIR / "manifest.json"

DAILY_SUCCESS = "daily_000001.SZ_20230101_20230115_success.json"
DAILY_503 = "daily_000001.SZ_20230101_20230115_upstream_pool_exhausted.json"
DAILY_429 = "daily_000001.SZ_20230101_20230115_rate_limited.json"
DATE_RANGE_400 = "date_range_too_large_000001.SZ_20130101_20240101.json"
ADJ_EMPTY = "adj_factor_000001.SZ_20230113_empty.json"
ADJ_NONEMPTY = "adj_factor_000001.SZ_20230113_nonempty.json"

DUMMY_TOKEN = "dummy-token-DO-NOT-LEAK-abc123-xyz789"


# ---------------------------------------------------------------------------
# Fixture loading helpers
# ---------------------------------------------------------------------------
def _manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text())


def _entry(filename: str) -> dict:
    return _manifest()["recordings"][filename]


def _body(filename: str) -> object:
    """Load one raw response body (the verbatim proxy JSON, no wrapper)."""
    return json.loads((FIXTURE_DIR / filename).read_text())


def _response(filename: str) -> tuple[int, object]:
    """Return ``(status_code, body)`` for one recorded specimen."""
    return _entry(filename)["status_code"], _body(filename)


def _key(filename: str):
    entry = _entry(filename)
    return fixture_key(entry["api_name"], entry["params"])


def _call(client: ProxyTushareClient, filename: str) -> dict:
    entry = _entry(filename)
    return client.fetch(entry["api_name"], **entry["params"])


def _replay_for(filename: str):
    return replay_transport({_key(filename): _response(filename)})


class _FakeHTTPResponse:
    """Minimal stand-in for the object ``urllib.request.urlopen`` yields."""

    def __init__(self, status: int, payload: bytes) -> None:
        self.status = status
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def __enter__(self) -> "_FakeHTTPResponse":
        return self

    def __exit__(self, *exc_info: object) -> bool:
        return False


class _CountingTransport:
    """Wraps a transport and records every call for retry-count assertions."""

    def __init__(self, inner) -> None:
        self._inner = inner
        self.calls: list[tuple[str, dict[str, str]]] = []

    def __call__(self, api_name, params):
        self.calls.append((api_name, dict(params)))
        return self._inner(api_name, params)

    @property
    def count(self) -> int:
        return len(self.calls)


class _SleepRecorder:
    """Injected in place of ``time.sleep`` so tests never actually wait."""

    def __init__(self) -> None:
        self.delays: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)


def _header(request: urllib.request.Request, name: str) -> str | None:
    for key, value in request.headers.items():
        if key.lower() == name.lower():
            return value
    return None


@pytest.fixture(autouse=True)
def _forbid_live_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tripwire: any test that reaches a real transport fails immediately."""

    def _boom(*args: object, **kwargs: object) -> object:
        raise AssertionError(
            "Test attempted a live network call; inject a transport instead."
        )

    monkeypatch.setattr(urllib.request, "urlopen", _boom)


# ---------------------------------------------------------------------------
# 1. Passthrough correctness
# ---------------------------------------------------------------------------
def test_fetch_returns_recorded_data_payload_verbatim() -> None:
    body = _body(DAILY_SUCCESS)
    client = ProxyTushareClient(transport=_replay_for(DAILY_SUCCESS))

    result = _call(client, DAILY_SUCCESS)

    assert result == body["data"]
    assert set(result) == {"fields", "items", "has_more"}
    assert result["fields"][0] == "ts_code"
    assert result["items"][0][0] == "000001.SZ"
    # Row order preserved exactly, nothing renamed/reshaped/dropped.
    assert [row[1] for row in result["items"]] == ["20230113", "20230112"]
    assert result["items"][0] == body["data"]["items"][0]
    assert result["items"][-1] == body["data"]["items"][-1]


def test_fetch_does_not_leak_the_envelope() -> None:
    client = ProxyTushareClient(transport=_replay_for(DAILY_SUCCESS))
    result = _call(client, DAILY_SUCCESS)
    # The caller sees the data payload, not the {"code", "data"} envelope.
    assert "code" not in result
    assert "data" not in result


# ---------------------------------------------------------------------------
# 2. Transport-neutral protocol
# ---------------------------------------------------------------------------
def test_proxy_client_structurally_satisfies_the_protocol() -> None:
    client = ProxyTushareClient(transport=_replay_for(DAILY_SUCCESS))
    assert isinstance(client, TushareClient)


def test_protocol_docstring_names_no_proxy_specific_thing() -> None:
    doc = (TushareClient.__doc__ or "") + (
        TushareClient.fetch.__doc__ or ""
    )
    for forbidden in (
        "ProxyTushareClient",
        "upstream_pool_exhausted",
        "rate_limited",
        "date_range_too_large",
        "pcd.mobcvb.cn",
        "X-API-Key",
        "TUSHARE_PROXY_TOKEN",
    ):
        assert forbidden not in doc, forbidden


# ---------------------------------------------------------------------------
# 3. Bounded retry with backoff on 429 / 503
# ---------------------------------------------------------------------------
def test_fetch_retries_upstream_pool_exhausted_then_succeeds() -> None:
    transport = _CountingTransport(
        replay_transport(
            {_key(DAILY_503): [_response(DAILY_503), _response(DAILY_SUCCESS)]}
        )
    )
    sleep = _SleepRecorder()
    client = ProxyTushareClient(transport=transport, sleep=sleep)

    result = _call(client, DAILY_SUCCESS)

    assert result == _body(DAILY_SUCCESS)["data"]
    assert transport.count == 2
    assert sleep.delays == [2.0]


def test_fetch_retries_rate_limited_then_succeeds() -> None:
    transport = _CountingTransport(
        replay_transport(
            {_key(DAILY_429): [_response(DAILY_429), _response(DAILY_SUCCESS)]}
        )
    )
    client = ProxyTushareClient(transport=transport, sleep=_SleepRecorder())

    result = _call(client, DAILY_SUCCESS)

    assert result == _body(DAILY_SUCCESS)["data"]
    assert transport.count == 2


def test_fetch_exceeding_retry_ceiling_raises_api_error() -> None:
    transport = _CountingTransport(_replay_for(DAILY_503))
    sleep = _SleepRecorder()
    client = ProxyTushareClient(transport=transport, sleep=sleep)

    with pytest.raises(TushareAPIError) as exc_info:
        _call(client, DAILY_503)

    assert transport.count == 4  # default max_attempts
    assert sleep.delays == [2.0, 4.0, 8.0]  # capped exponential backoff
    error = exc_info.value
    assert error.status_code == 503
    assert error.error == "upstream_pool_exhausted"
    assert "exhausted 4 attempts" in str(error)


def test_retry_policy_is_configurable() -> None:
    transport = _CountingTransport(_replay_for(DAILY_503))
    sleep = _SleepRecorder()
    client = ProxyTushareClient(
        transport=transport, max_attempts=2, base_backoff=0.01, sleep=sleep
    )
    with pytest.raises(TushareAPIError):
        _call(client, DAILY_503)
    assert transport.count == 2
    assert sleep.delays == [0.01]


# ---------------------------------------------------------------------------
# 4. date_range_too_large is surfaced, never retried
# ---------------------------------------------------------------------------
def test_date_range_too_large_raises_without_retrying() -> None:
    transport = _CountingTransport(_replay_for(DATE_RANGE_400))
    sleep = _SleepRecorder()
    client = ProxyTushareClient(transport=transport, sleep=sleep)

    with pytest.raises(TushareAPIError) as exc_info:
        _call(client, DATE_RANGE_400)

    assert transport.count == 1  # a 400 must never be retried
    assert sleep.delays == []
    error = exc_info.value
    assert error.status_code == 400
    assert error.error == "date_range_too_large"
    # The proxy's own message is preserved verbatim.
    assert "date range exceeds 366 days; paginate the request" in str(error)


# ---------------------------------------------------------------------------
# 5. Empty-success handling (opt-in)
# ---------------------------------------------------------------------------
def test_retry_on_empty_true_returns_nonempty_after_empty() -> None:
    transport = _CountingTransport(
        replay_transport(
            {_key(ADJ_EMPTY): [_response(ADJ_EMPTY), _response(ADJ_NONEMPTY)]}
        )
    )
    sleep = _SleepRecorder()
    client = ProxyTushareClient(transport=transport, sleep=sleep)

    entry = _entry(ADJ_EMPTY)
    result = client.fetch(
        entry["api_name"], retry_on_empty=True, **entry["params"]
    )

    assert result == _body(ADJ_NONEMPTY)["data"]
    assert result["items"]
    assert transport.count == 2
    assert sleep.delays == [2.0]


def test_retry_on_empty_false_returns_empty_immediately() -> None:
    transport = _CountingTransport(
        replay_transport(
            {_key(ADJ_EMPTY): [_response(ADJ_EMPTY), _response(ADJ_NONEMPTY)]}
        )
    )
    sleep = _SleepRecorder()
    client = ProxyTushareClient(transport=transport, sleep=sleep)

    entry = _entry(ADJ_EMPTY)
    result = client.fetch(entry["api_name"], **entry["params"])

    assert result == _body(ADJ_EMPTY)["data"]
    assert result["items"] == []
    assert transport.count == 1  # default is opt-out: no retry, no sleep
    assert sleep.delays == []


def test_retry_on_empty_true_exhausted_raises_empty_response_error() -> None:
    transport = _CountingTransport(_replay_for(ADJ_EMPTY))
    sleep = _SleepRecorder()
    client = ProxyTushareClient(
        transport=transport, max_attempts=3, sleep=sleep
    )

    entry = _entry(ADJ_EMPTY)
    with pytest.raises(TushareEmptyResponseError) as exc_info:
        client.fetch(entry["api_name"], retry_on_empty=True, **entry["params"])

    assert transport.count == 3
    assert sleep.delays == [2.0, 4.0]
    assert exc_info.value.error == "empty_response"
    assert "empty payload" in str(exc_info.value)


# ---------------------------------------------------------------------------
# 6. Canonical-consistency recording safeguard
# ---------------------------------------------------------------------------
def test_check_canonical_consistency_raises_on_differing_payloads() -> None:
    first = {"fields": ["ts_code"], "items": [["000001.SZ"]]}
    second = {"fields": ["ts_code"], "items": [["600000.SH"]]}

    with pytest.raises(TushareNonDeterministicResponseError) as exc_info:
        check_canonical_consistency(
            "daily", {"ts_code": "000001.SZ"}, [first, second]
        )

    error = exc_info.value
    assert error.api_name == "daily"
    assert error.payloads == [first, second]
    # The error names the request and both payloads.
    assert "000001.SZ" in str(error)
    assert "600000.SH" in str(error)
    assert "daily" in str(error)


def test_check_canonical_consistency_returns_reference_when_identical() -> None:
    payload = {"fields": ["ts_code"], "items": [["000001.SZ"]]}
    assert check_canonical_consistency("daily", {}, [payload, payload]) is payload


def test_check_canonical_consistency_ignores_empty_payloads() -> None:
    empty = {"fields": ["ts_code"], "items": []}
    payload = {"fields": ["ts_code"], "items": [["000001.SZ"]]}
    # An intermittently-empty response is a retry concern, not a conflict.
    assert check_canonical_consistency("adj_factor", {}, [empty, payload]) is payload


def test_check_canonical_consistency_all_empty_returns_first() -> None:
    empty = {"fields": ["ts_code"], "items": []}
    assert check_canonical_consistency("adj_factor", {}, [empty]) == empty


def test_record_fixture_returns_canonical_response() -> None:
    body = {"code": 0, "data": {"fields": ["a"], "items": [["x"]]}}
    calls: list[tuple[str, dict[str, str]]] = []

    def transport(api_name, params):
        calls.append((api_name, dict(params)))
        return 200, body

    status, recorded = record_fixture(transport, "daily", samples=3, ts_code="000001.SZ")

    assert status == 200
    assert recorded == body
    assert len(calls) == 3


def test_record_fixture_raises_on_a_nondeterministic_transport() -> None:
    first = {"fields": ["a"], "items": [["x"]]}
    second = {"fields": ["a"], "items": [["y"]]}
    responses = [
        (200, {"code": 0, "data": first}),
        (200, {"code": 0, "data": first}),
        (200, {"code": 0, "data": second}),
    ]
    iterator = iter(responses)
    calls: list[object] = []

    def transport(api_name, params):
        calls.append((api_name, dict(params)))
        return next(iterator)

    with pytest.raises(TushareNonDeterministicResponseError):
        record_fixture(transport, "daily", samples=3, ts_code="000001.SZ")

    assert len(calls) == 3  # it compared every sample before giving up


def test_client_record_delegates_to_the_recording_helper() -> None:
    body = _body(DAILY_SUCCESS)
    transport = _CountingTransport(
        replay_transport({_key(DAILY_SUCCESS): _response(DAILY_SUCCESS)})
    )
    client = ProxyTushareClient(transport=transport)

    status, recorded = client.record(
        "daily", samples=2, **_entry(DAILY_SUCCESS)["params"]
    )

    assert status == 200
    assert recorded == body
    assert transport.count == 2


# ---------------------------------------------------------------------------
# 7. Token handling
# ---------------------------------------------------------------------------
def test_missing_token_without_transport_raises_before_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(TOKEN_ENV_VAR, raising=False)
    client = ProxyTushareClient()
    with pytest.raises(TushareAPIError) as exc_info:
        client.fetch("daily", ts_code="000001.SZ")
    assert exc_info.value.error == "missing_token"


def test_injected_transport_needs_no_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(TOKEN_ENV_VAR, raising=False)
    client = ProxyTushareClient(transport=_replay_for(DAILY_SUCCESS))
    assert _call(client, DAILY_SUCCESS) == _body(DAILY_SUCCESS)["data"]


def test_token_is_read_at_call_time_not_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(TOKEN_ENV_VAR, raising=False)
    client = ProxyTushareClient(base_url="https://example.test/tushare/pro")
    captured: dict[str, object] = {}

    def fake_urlopen(request, *args, **kwargs):
        captured["key"] = _header(request, "X-API-Key")
        return _FakeHTTPResponse(
            200, json.dumps(_body(DAILY_SUCCESS)).encode()
        )

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    # Token set *after* construction must still be picked up.
    monkeypatch.setenv(TOKEN_ENV_VAR, DUMMY_TOKEN)

    assert _call(client, DAILY_SUCCESS) == _body(DAILY_SUCCESS)["data"]
    assert captured["key"] == DUMMY_TOKEN


def test_live_transport_posts_with_x_api_key_header_and_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(TOKEN_ENV_VAR, "secret-key")
    captured: dict[str, object] = {}
    body = _body(DAILY_SUCCESS)

    def fake_urlopen(request, *args, **kwargs):
        captured["url"] = request.full_url
        captured["method"] = request.get_method()
        captured["key"] = _header(request, "X-API-Key")
        captured["content_type"] = _header(request, "Content-Type")
        captured["data"] = request.data
        return _FakeHTTPResponse(200, json.dumps(body).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    client = ProxyTushareClient(base_url="https://example.test/tushare/pro")

    assert _call(client, DAILY_SUCCESS) == body["data"]
    entry = _entry(DAILY_SUCCESS)
    assert captured["url"] == "https://example.test/tushare/pro/daily"
    assert captured["method"] == "POST"
    assert captured["key"] == "secret-key"
    assert captured["content_type"] == "application/json"
    assert json.loads(captured["data"]) == entry["params"]
    # The token travels only in the header, never in the URL.
    assert "secret-key" not in str(captured["url"])


def test_token_never_appears_in_any_exception_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(TOKEN_ENV_VAR, DUMMY_TOKEN)
    raised: list[TushareAPIError] = []

    def exercise(fake_urlopen, **fetch_kwargs) -> None:
        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        client = ProxyTushareClient(
            base_url="https://example.test/tushare/pro",
            max_attempts=2,
            sleep=_SleepRecorder(),
        )
        with pytest.raises(TushareAPIError) as exc_info:
            client.fetch("daily", ts_code="000001.SZ", **fetch_kwargs)
        raised.append(exc_info.value)

    # Transient failure, exhausted.
    exercise(
        lambda request, *a, **k: _FakeHTTPResponse(
            503, b'{"ok": false, "error": "upstream_pool_exhausted"}'
        )
    )
    # Non-retryable rejection.
    exercise(
        lambda request, *a, **k: _FakeHTTPResponse(
            400,
            b'{"ok": false, "error": "date_range_too_large", '
            b'"message": "date range exceeds 366 days; paginate the request"}',
        )
    )
    # Malformed success.
    exercise(
        lambda request, *a, **k: _FakeHTTPResponse(200, b'{"unexpected": true}')
    )
    # Raised HTTPError path.
    def raise_http_error(request, *a, **k):
        raise urllib.error.HTTPError(
            request.full_url,
            503,
            "Service Unavailable",
            None,
            io.BytesIO(b'{"ok": false, "error": "upstream_pool_exhausted"}'),
        )

    exercise(raise_http_error)

    assert len(raised) == 4
    for error in raised:
        rendered = " ".join(
            [str(error), repr(error), repr(error.args), repr(error.body)]
        )
        assert DUMMY_TOKEN not in rendered
        assert "secret" not in rendered.lower()


def test_empty_response_error_does_not_leak_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(TOKEN_ENV_VAR, DUMMY_TOKEN)
    client = ProxyTushareClient(
        transport=_replay_for(ADJ_EMPTY), max_attempts=2, sleep=_SleepRecorder()
    )
    entry = _entry(ADJ_EMPTY)
    with pytest.raises(TushareEmptyResponseError) as exc_info:
        client.fetch(entry["api_name"], retry_on_empty=True, **entry["params"])
    assert DUMMY_TOKEN not in str(exc_info.value)


# ---------------------------------------------------------------------------
# 8. replay_transport behavior
# ---------------------------------------------------------------------------
def test_replay_transport_raises_key_error_for_unrecorded_request() -> None:
    transport = replay_transport({})
    with pytest.raises(KeyError) as exc_info:
        transport("daily", {"ts_code": "000001.SZ"})
    assert "daily" in str(exc_info.value)


def test_replay_transport_key_ignores_param_order() -> None:
    response = _response(DAILY_SUCCESS)
    transport = replay_transport({_key(DAILY_SUCCESS): response})
    reordered = {
        "end_date": "20230115",
        "ts_code": "000001.SZ",
        "start_date": "20230101",
    }
    assert transport("daily", reordered) == response


def test_replay_transport_serves_a_sequence_in_order() -> None:
    failure = _response(DAILY_503)
    success = _response(DAILY_SUCCESS)
    transport = replay_transport({_key(DAILY_503): [failure, success]})
    entry = _entry(DAILY_503)

    assert transport("daily", entry["params"]) == failure
    assert transport("daily", entry["params"]) == success
    assert transport("daily", entry["params"]) == success  # repeat_last default


def test_replay_transport_repeat_last_false_raises_when_exhausted() -> None:
    failure = _response(DAILY_503)
    transport = replay_transport(
        {_key(DAILY_503): [failure]}, repeat_last=False
    )
    entry = _entry(DAILY_503)
    assert transport("daily", entry["params"]) == failure
    with pytest.raises(KeyError):
        transport("daily", entry["params"])


# ---------------------------------------------------------------------------
# 9. Fixture integrity and provenance
# ---------------------------------------------------------------------------
def test_every_manifest_recording_has_a_readable_json_body() -> None:
    recordings = _manifest()["recordings"]
    assert recordings, "fixture manifest is empty"
    for filename, entry in recordings.items():
        assert (FIXTURE_DIR / filename).is_file(), filename
        _body(filename)  # must parse as JSON
        assert isinstance(entry["api_name"], str)
        assert isinstance(entry["params"], dict)
        assert isinstance(entry["status_code"], int)


def test_fixture_provenance_is_explicitly_labeled() -> None:
    provenance = _manifest()["_provenance"]
    # These are contract-modeled test vectors, not live captures; the manifest
    # must say so plainly rather than implying a real recording.
    assert provenance["live_recorded"] is False
    assert provenance["reason"]


def test_non_200_specimens_are_exactly_the_expected_failures() -> None:
    recordings = _manifest()["recordings"]
    non_200 = {
        filename: entry["status_code"]
        for filename, entry in recordings.items()
        if entry["status_code"] != 200
    }
    assert non_200 == {
        DAILY_503: 503,
        DAILY_429: 429,
        DATE_RANGE_400: 400,
    }
