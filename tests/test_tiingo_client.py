"""Tests for the raw Tiingo HTTP client (Phase 4B, P4B-1).

Every test here is offline. Data calls always go through an injected
transport -- either :func:`replay_transport` over the recorded specimens in
``tests/fixtures/tiingo/client/`` or an inline spy. An autouse fixture
additionally replaces ``urllib.request.urlopen`` with a tripwire, so an
accidental live call fails the offending test loudly instead of silently
hitting the network. Only :class:`TiingoClient`'s own transport is exercised
with a fake ``urlopen`` (to prove auth/status handling); no test reaches
``https://api.tiingo.com``.
"""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from smart_beta.vendors.tiingo.client import (
    BASE_URL,
    TiingoAPIError,
    TiingoClient,
    TiingoConfigError,
    replay_transport,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "tiingo" / "client"
MANIFEST_PATH = FIXTURE_DIR / "manifest.json"


# ---------------------------------------------------------------------------
# Fixture loading helpers
# ---------------------------------------------------------------------------
def _manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text())


def _body(filename: str) -> object:
    """Load one raw response body (the verbatim Tiingo JSON, no wrapper)."""
    return json.loads((FIXTURE_DIR / filename).read_text())


def _recorded(filename: str) -> tuple[str, int, object]:
    """Return ``(url_path, status_code, body)`` for one recorded specimen."""
    entry = _manifest()["recordings"][filename]
    return entry["url_path"], entry["status_code"], _body(filename)


def _replay_for(filename: str):
    """Build a fixture-replay client for a single recorded specimen."""
    path, status, body = _recorded(filename)
    transport = replay_transport({path: (status, body)})
    return TiingoClient(transport=transport), body


def _replay_transport_for(filename: str):
    """A replay transport carrying exactly one recorded specimen."""
    path, status, body = _recorded(filename)
    return replay_transport({path: (status, body)})


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


@pytest.fixture(autouse=True)
def _forbid_live_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tripwire: any test that reaches a real transport fails immediately.

    Tests of the live transport override this by monkeypatching ``urlopen``
    again inside the test body (the later ``setattr`` wins).
    """

    def _boom(*args: object, **kwargs: object) -> object:
        raise AssertionError(
            "Test attempted a live network call; inject a transport instead."
        )

    monkeypatch.setattr(urllib.request, "urlopen", _boom)


# ---------------------------------------------------------------------------
# 1. Passthrough correctness
# ---------------------------------------------------------------------------
def test_get_eod_prices_returns_recorded_body_verbatim() -> None:
    client, expected = _replay_for("aapl_eod_prices_2020-08-20_2020-09-05.json")
    result = client.get_eod_prices("AAPL", "2020-08-20", "2020-09-05")

    assert result == expected
    assert len(result) == 12
    # Ordering preserved exactly (Tiingo returns chronological rows).
    assert [row["date"] for row in result] == [row["date"] for row in expected]
    # Every field survives unchanged; nothing renamed/reshaped/dropped.
    assert result[0] == expected[0]
    assert result[-1] == expected[-1]
    assert set(result[0]) == set(expected[0])
    # The split specimen really spans the 4-for-1 split.
    assert expected[7]["splitFactor"] == 4.0
    assert expected[6]["splitFactor"] == 1.0


def test_get_meta_returns_recorded_body_verbatim() -> None:
    client, expected = _replay_for("aapl_meta.json")
    result = client.get_meta("AAPL")
    assert result == expected
    assert result["ticker"] == "AAPL"
    # Live GET /tiingo/daily/{ticker} has no permanent-identity field.
    assert "permaTicker" not in result
    assert "cik" not in result


def test_get_meta_answers_for_delisted_security() -> None:
    client, expected = _replay_for("twtr_meta.json")
    result = client.get_meta("TWTR")
    assert result == expected
    assert result["ticker"] == "TWTR"
    assert "permaTicker" not in result
    assert "cik" not in result


def test_get_fundamentals_asreported_returns_recorded_body_verbatim() -> None:
    client, expected = _replay_for("aapl_fundamentals_asreported.json")
    result = client.get_fundamentals_asreported("AAPL", "2026-01-01", "2026-12-31")
    assert result == expected
    assert all("statementData" in item for item in result)
    # Live shape: fiscal identity is year/quarter, date is the filing date.
    assert result[0]["year"] == 2026
    assert result[0]["quarter"] == 3
    assert result[0]["date"] == "2026-07-31"
    assert "fiscalYear" not in result[0]


def test_get_fundamentals_normalized_returns_recorded_body_verbatim() -> None:
    client, expected = _replay_for("aapl_fundamentals_normalized.json")
    result = client.get_fundamentals_normalized("AAPL", "2026-01-01", "2026-12-31")
    assert result == expected
    # Live shape: date is the fiscal period end (not a timestamp, not 6/30).
    assert result[0]["date"] == "2026-06-27"
    assert result[0]["year"] == 2026
    assert result[0]["quarter"] == 3


def test_get_fundamentals_daily_returns_recorded_body_verbatim() -> None:
    client, expected = _replay_for(
        "aapl_fundamentals_daily_2024-01-02_2024-01-05.json"
    )
    result = client.get_fundamentals_daily("AAPL", "2024-01-02", "2024-01-05")

    assert result == expected
    assert len(result) == 4
    # Ordering preserved exactly (Tiingo returns chronological rows).
    assert [row["date"] for row in result] == [row["date"] for row in expected]
    # Every field survives unchanged; nothing renamed/reshaped/dropped.
    assert set(result[0]) == {
        "date",
        "marketCap",
        "enterpriseVal",
        "peRatio",
        "pbRatio",
        "trailingPEG1Y",
    }
    assert result[0] == expected[0]
    assert result[-1] == expected[-1]
    # This endpoint returns a real marketCap; the client does not interpret it.
    assert expected[0]["marketCap"] == pytest.approx(2887212881280.0)


# ---------------------------------------------------------------------------
# 2. Config error on missing key
# ---------------------------------------------------------------------------
def test_missing_key_and_no_transport_raises_config_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TIINGO_API_KEY", raising=False)
    with pytest.raises(TiingoConfigError):
        TiingoClient()


def test_explicit_api_key_without_transport_is_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TIINGO_API_KEY", raising=False)
    # Must not raise: a key is available for the live transport.
    TiingoClient(api_key="explicit-key")


def test_env_api_key_is_used_when_no_explicit_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TIINGO_API_KEY", "env-key")
    TiingoClient()  # must not raise


def test_injected_transport_without_any_key_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TIINGO_API_KEY", raising=False)
    path, status, body = _recorded("aapl_meta.json")
    client = TiingoClient(transport=replay_transport({path: (status, body)}))
    assert client.get_meta("AAPL") == body


# ---------------------------------------------------------------------------
# 3. Non-200 becomes TiingoAPIError
# ---------------------------------------------------------------------------
def test_recorded_rgen_400_becomes_api_error() -> None:
    path, status, body = _recorded("rgen_fundamentals_asreported_error.json")
    assert status == 400  # the recorded specimen really is the failing call
    client = TiingoClient(transport=replay_transport({path: (status, body)}))

    with pytest.raises(TiingoAPIError) as exc_info:
        client.get_fundamentals_asreported("RGEN", "2024-01-01", "2024-12-31")

    error = exc_info.value
    assert error.status_code == 400
    assert error.body == body
    assert error.path == "/tiingo/fundamentals/RGEN/statements"
    assert error.params["asReported"] == "true"
    # The real captured error detail is surfaced, not a synthesized message.
    assert error.body["detail"] in str(error)


# ---------------------------------------------------------------------------
# 4. asReported=true is passed, and only for the asReported method
# ---------------------------------------------------------------------------
def test_asreported_param_passed_only_for_asreported_method() -> None:
    calls: list[tuple[str, dict[str, str]]] = []

    def spy(path: str, params) -> tuple[int, object]:
        calls.append((path, dict(params)))
        return 200, []

    client = TiingoClient(transport=spy)
    client.get_fundamentals_asreported("AAPL", "2025-01-01", "2026-12-31")
    client.get_fundamentals_normalized("AAPL", "2025-01-01", "2026-12-31")

    assert len(calls) == 2
    assert calls[0][1]["asReported"] == "true"
    assert "asReported" not in calls[1][1]
    # Same endpoint family / same date params; only asReported differs.
    assert calls[0][0] == calls[1][0] == "/tiingo/fundamentals/AAPL/statements"
    assert calls[0][1]["startDate"] == calls[1][1]["startDate"] == "2025-01-01"
    assert calls[0][1]["endDate"] == calls[1][1]["endDate"] == "2026-12-31"


def test_fundamentals_date_params_are_optional() -> None:
    calls: list[dict[str, str]] = []

    def spy(path: str, params) -> tuple[int, object]:
        calls.append(dict(params))
        return 200, []

    client = TiingoClient(transport=spy)
    client.get_fundamentals_asreported("AAPL")
    client.get_fundamentals_normalized("AAPL")

    assert calls[0] == {"asReported": "true"}
    assert calls[1] == {}


def test_fundamentals_daily_date_params_are_optional() -> None:
    calls: list[tuple[str, dict[str, str]]] = []

    def spy(path: str, params) -> tuple[int, object]:
        calls.append((path, dict(params)))
        return 200, []

    client = TiingoClient(transport=spy)
    client.get_fundamentals_daily("AAPL")
    client.get_fundamentals_daily("AAPL", "2024-01-02", "2024-01-05")

    assert calls == [
        ("/tiingo/fundamentals/AAPL/daily", {}),
        (
            "/tiingo/fundamentals/AAPL/daily",
            {"startDate": "2024-01-02", "endDate": "2024-01-05"},
        ),
    ]


# ---------------------------------------------------------------------------
# 5. replay_transport never fabricates a response
# ---------------------------------------------------------------------------
def test_replay_transport_raises_key_error_for_unrecorded_path() -> None:
    transport = replay_transport({"/tiingo/daily/AAPL": (200, {})})
    with pytest.raises(KeyError) as exc_info:
        transport("/tiingo/daily/MSFT", {})
    assert "/tiingo/daily/MSFT" in str(exc_info.value)


def test_replay_transport_keys_on_path_only() -> None:
    body = [{"date": "2020-08-31T00:00:00.000Z"}]
    transport = replay_transport({"/tiingo/daily/AAPL/prices": (200, body)})
    status, returned = transport("/tiingo/daily/AAPL/prices", {"startDate": "x"})
    assert status == 200
    assert returned == body


def test_replay_transport_returns_the_recorded_tuple() -> None:
    transport = replay_transport({"/tiingo/daily/AAPL": (200, {"ticker": "AAPL"})})
    status, body = transport("/tiingo/daily/AAPL", {})
    assert status == 200
    assert body == {"ticker": "AAPL"}


# ---------------------------------------------------------------------------
# 6. No live network: the autouse tripwire plus an explicit sweep
# ---------------------------------------------------------------------------
def test_all_data_methods_run_offline_through_replay() -> None:
    """Sweep every recorded specimen through the method it belongs to, with
    the urlopen tripwire installed. Success proves none reaches the network."""
    manifest = _manifest()["recordings"]

    eod = TiingoClient(transport=_replay_transport_for(
        "aapl_eod_prices_2020-08-20_2020-09-05.json"
    ))
    assert len(eod.get_eod_prices("AAPL", "2020-08-20", "2020-09-05")) == 12

    meta = TiingoClient(transport=_replay_transport_for("aapl_meta.json"))
    assert meta.get_meta("AAPL")["ticker"] == "AAPL"

    asrep = TiingoClient(
        transport=_replay_transport_for("aapl_fundamentals_asreported.json")
    )
    assert asrep.get_fundamentals_asreported("AAPL")

    norm = TiingoClient(
        transport=_replay_transport_for("aapl_fundamentals_normalized.json")
    )
    assert norm.get_fundamentals_normalized("AAPL")

    daily = TiingoClient(
        transport=_replay_transport_for(
            "aapl_fundamentals_daily_2024-01-02_2024-01-05.json"
        )
    )
    assert daily.get_fundamentals_daily("AAPL", "2024-01-02", "2024-01-05")

    # Sanity: the manifest really covers all seven recorded specimens.
    assert len(manifest) == 7


# ---------------------------------------------------------------------------
# Live-transport plumbing (fake urlopen only -- still no real network)
# ---------------------------------------------------------------------------
def test_live_transport_attaches_token_auth_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, str] = {}

    def fake_urlopen(request: urllib.request.Request, *args: object, **kwargs: object):
        captured["url"] = request.full_url
        captured["auth"] = request.get_header("Authorization")
        return _FakeHTTPResponse(200, b'{"ticker": "AAPL"}')

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    client = TiingoClient(api_key="secret-key")

    assert client.get_meta("AAPL") == {"ticker": "AAPL"}
    assert captured["auth"] == "Token secret-key"
    assert captured["url"] == f"{BASE_URL}/tiingo/daily/AAPL"


def test_live_transport_urlencodes_query_params(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, str] = {}

    def fake_urlopen(request: urllib.request.Request, *args: object, **kwargs: object):
        captured["url"] = request.full_url
        return _FakeHTTPResponse(200, b"[]")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    client = TiingoClient(api_key="k")
    client.get_eod_prices("AAPL", "2020-08-20", "2020-09-05")

    assert "startDate=2020-08-20" in captured["url"]
    assert "endDate=2020-09-05" in captured["url"]


def test_live_transport_http_error_becomes_api_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_urlopen(request: urllib.request.Request, *args: object, **kwargs: object):
        raise urllib.error.HTTPError(
            request.full_url,
            400,
            "Bad Request",
            None,
            io.BytesIO(b'{"detail": "nope"}'),
        )

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    client = TiingoClient(api_key="k")

    with pytest.raises(TiingoAPIError) as exc_info:
        client.get_meta("RGEN")

    assert exc_info.value.status_code == 400
    assert exc_info.value.body == {"detail": "nope"}


def test_live_transport_non_json_error_body_does_not_crash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_urlopen(request: urllib.request.Request, *args: object, **kwargs: object):
        return _FakeHTTPResponse(503, b"<html>upstream down</html>")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    client = TiingoClient(api_key="k")

    with pytest.raises(TiingoAPIError) as exc_info:
        client.get_meta("AAPL")

    assert exc_info.value.status_code == 503
    assert exc_info.value.body == "<html>upstream down</html>"


# ---------------------------------------------------------------------------
# Fixture-integrity guards
# ---------------------------------------------------------------------------
def test_every_manifest_entry_has_a_readable_json_body() -> None:
    recordings = _manifest()["recordings"]
    assert recordings, "fixture manifest is empty"
    for filename, entry in recordings.items():
        assert (FIXTURE_DIR / filename).is_file(), filename
        _body(filename)  # must parse as JSON
        assert entry["url_path"].startswith("/tiingo/")
        assert isinstance(entry["status_code"], int)


def test_error_specimen_is_the_only_non_200_recording() -> None:
    recordings = _manifest()["recordings"]
    non_200 = {
        name: entry["status_code"]
        for name, entry in recordings.items()
        if entry["status_code"] != 200
    }
    assert non_200 == {"rgen_fundamentals_asreported_error.json": 400}


def test_daily_meta_specimens_have_no_permanent_identity_field() -> None:
    """Live GET /tiingo/daily/{ticker} does not expose permaTicker or CIK."""
    expected_keys = {
        "ticker",
        "name",
        "description",
        "startDate",
        "endDate",
        "exchangeCode",
    }
    for filename in ("aapl_meta.json", "twtr_meta.json"):
        _, _, body = _recorded(filename)
        assert set(body) == expected_keys, filename


def test_fundamentals_daily_manifest_entry_matches_recorded_shape() -> None:
    """The new fundamentals-daily specimen is registered like every other
    fixture, and its body carries Tiingo's native per-day keys verbatim."""
    filename = "aapl_fundamentals_daily_2024-01-02_2024-01-05.json"
    url_path, status, body = _recorded(filename)
    assert url_path == "/tiingo/fundamentals/AAPL/daily"
    assert status == 200
    assert len(body) == 4
    assert all(
        set(row)
        == {
            "date",
            "marketCap",
            "enterpriseVal",
            "peRatio",
            "pbRatio",
            "trailingPEG1Y",
        }
        for row in body
    )
