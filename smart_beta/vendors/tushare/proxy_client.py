"""Proxy-backed Tushare transport (Phase 4D-B, P4DB-1).

This is the lowest layer of the Phase 4D-B China A-share adapter and the only
place in the phase that may talk to the network. It receives a request, reaches
the third-party Tushare proxy, and returns the raw ``{"fields": [...],
"items": [...]}`` payload. It performs **no** PIT mapping, schema validation,
knowledge-date parsing, or identifier resolution -- every later task consumes
it through the transport-neutral
:class:`~smart_beta.vendors.tushare.client.TushareClient` protocol.

Everything proxy-specific is confined to this file:

* the live HTTPS request (``POST {base_url}/{api_name}`` with an
  ``X-API-Key`` header, the key read from the ``TUSHARE_PROXY_TOKEN``
  environment variable at call time, never at import time and never logged);
* bounded retry with exponential backoff on the proxy's transient
  ``rate_limited`` (429) / ``upstream_pool_exhausted`` (503) responses;
* the proxy's non-retryable ``date_range_too_large`` (400) rejection,
  surfaced intact instead of being retried or swallowed;
* opt-in retry of the proxy's intermittent ``code: 0``-with-empty-``items``
  success responses, then a loud :class:`TushareEmptyResponseError`;
* :func:`replay_transport`, the offline fixture-replay transport every test
  in this phase (not just this task's) uses, and the recording-time canonical
  consistency check (:func:`record_fixture` /
  :class:`~smart_beta.vendors.tushare.client.TushareNonDeterministicResponseError`).

No proxy-specific class or exception name is used by any caller: every other
module talks only to :class:`TushareClient`. That is what makes a future
official-API transport a drop-in replacement.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from argparse import ArgumentParser
from pathlib import Path
from typing import Callable, Mapping, Sequence

from smart_beta.vendors.tushare.client import (
    Transport,
    TushareAPIError,
    TushareClient,
    TushareEmptyResponseError,
    TushareNonDeterministicResponseError,
)

__all__ = [
    "PROXY_BASE_URL",
    "TOKEN_ENV_VAR",
    "DEFAULT_MAX_ATTEMPTS",
    "DEFAULT_BASE_BACKOFF_SECONDS",
    "DEFAULT_MAX_BACKOFF_SECONDS",
    "RETRYABLE_STATUS_CODES",
    "RETRYABLE_ERROR_CODES",
    "RequestKey",
    "check_canonical_consistency",
    "record_fixture",
    "fixture_key",
    "replay_transport",
    "ProxyTushareClient",
    # Re-exported so a transport implementer imports the protocol from the
    # same place as the concrete client it matches.
    "TushareClient",
    "main",
]

#: Root of the proxy's Tushare surface. API names are appended as a path
#: segment (``{base_url}/{api_name}``), per the proxy's own OpenAPI schema.
PROXY_BASE_URL = "https://pcd.mobcvb.cn/tushare/pro"

#: Environment variable the bearer token is read from, at call time.
TOKEN_ENV_VAR = "TUSHARE_PROXY_TOKEN"

#: Retry ceiling. Four attempts spans the empirically-observed ~10-15s
#: recovery window with base 2s / cap 8s backoff (2 + 4 + 8 = 14s of sleep).
DEFAULT_MAX_ATTEMPTS = 4
DEFAULT_BASE_BACKOFF_SECONDS = 2.0
DEFAULT_MAX_BACKOFF_SECONDS = 8.0

#: Transient transport conditions worth retrying.
RETRYABLE_STATUS_CODES = frozenset({429, 503})
RETRYABLE_ERROR_CODES = frozenset({"rate_limited", "upstream_pool_exhausted"})

#: ``(api_name, sorted params)`` identity of one logical request.
RequestKey = tuple[str, tuple[tuple[str, str], ...]]


# ---------------------------------------------------------------------------
# Small shared helpers
# ---------------------------------------------------------------------------
def _coerce_params(params: Mapping[str, object]) -> dict[str, str]:
    """Normalize every parameter value to ``str`` so request identity,
    serialization, and fixture keys agree regardless of how a caller typed
    the value (Tushare parameters are query-style strings)."""
    return {str(key): str(value) for key, value in params.items()}


def _decode_body(raw: bytes) -> object:
    """Parse a response body as JSON, falling back to decoded text so a
    non-JSON error page is still diagnosable rather than crashing."""
    if not raw:
        return None
    text = raw.decode("utf-8", errors="replace")
    try:
        return json.loads(text)
    except ValueError:
        return text


def _error_code(body: object) -> str | None:
    if isinstance(body, Mapping):
        error = body.get("error")
        return error if isinstance(error, str) else None
    return None


def _error_message(
    api_name: str, params: Mapping[str, str], status: int, body: object
) -> str:
    """Build a diagnostic message from the response, preserving the proxy's
    own ``error``/``message``/``msg`` text verbatim. Authentication material
    is never part of ``params`` or ``body`` and so can never leak here."""
    parts = [
        f"Tushare request api_name={api_name!r} params={dict(params)!r} "
        f"returned status {status}"
    ]
    if isinstance(body, Mapping):
        for key in ("error", "message", "msg"):
            value = body.get(key)
            if value:
                parts.append(f"{key}={value!r}")
    parts.append(f"body={body!r}")
    return "; ".join(parts)


def _is_retryable(status: int, body: object) -> bool:
    """Transient by status code, or by the proxy's own error envelope."""
    if status in RETRYABLE_STATUS_CODES:
        return True
    return (
        isinstance(body, Mapping)
        and body.get("ok") is False
        and body.get("error") in RETRYABLE_ERROR_CODES
    )


def _payload_items(payload: object) -> list | None:
    """Return ``payload['items']`` when payload looks like a data payload
    (an object with a list ``items``), else ``None``."""
    if isinstance(payload, Mapping):
        items = payload.get("items")
        if isinstance(items, list):
            return items
    return None


def _canonical_bytes(payload: object) -> str:
    """Stable serialization used for the recording-time consistency check.

    Field/row ordering is intentionally *not* sorted: the check exists to
    catch a request that returns genuinely different data, and reordering is
    itself a difference worth surfacing rather than normalizing away.
    """
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)


# ---------------------------------------------------------------------------
# Recording-time canonical consistency
# ---------------------------------------------------------------------------
def check_canonical_consistency(
    api_name: str,
    params: Mapping[str, object],
    payloads: Sequence[object],
) -> object:
    """Return the canonical non-empty payload, or raise if two disagree.

    Empty payloads (``items == []``) are ignored: an intermittently-empty
    response is a retry concern, not evidence of conflicting data. Every
    non-empty payload must serialize identically; the first two that differ
    cause a :class:`TushareNonDeterministicResponseError` naming the request
    and both payloads. Never silently picks one.

    This is a pure function so the safeguard itself is unit-testable with
    synthetic payloads, with no network involved.
    """
    normalized = _coerce_params(params)
    payload_list = list(payloads)
    non_empty = [p for p in payload_list if _payload_items(p)]
    if not non_empty:
        # Nothing to compare -- if everything was empty, hand back the first
        # payload so a caller can still record "this really was empty".
        return payload_list[0] if payload_list else {}
    reference = non_empty[0]
    reference_bytes = _canonical_bytes(reference)
    for other in non_empty[1:]:
        if _canonical_bytes(other) != reference_bytes:
            raise TushareNonDeterministicResponseError(
                api_name, normalized, [reference, other]
            )
    return reference


def record_fixture(
    transport: Transport,
    api_name: str,
    *,
    samples: int = 3,
    **params: str,
) -> tuple[int, object]:
    """Issue one request ``samples`` times through ``transport`` and return
    the canonical raw ``(status_code, body)`` response to record.

    Non-empty success payloads seen across the samples must agree byte-for-
    byte (see :func:`check_canonical_consistency`); any disagreement raises
    :class:`TushareNonDeterministicResponseError`. Transient failures
    (429/503) are recorded as-is, exactly like a live call.

    This is a recording-time safeguard only: normal replay-driven test
    execution never calls it.
    """
    if samples < 1:
        raise ValueError("samples must be >= 1")
    normalized = _coerce_params(params)
    responses = [transport(api_name, normalized) for _ in range(samples)]

    payloads: list[object] = []
    for status, body in responses:
        if status == 200 and isinstance(body, Mapping):
            data = body.get("data")
            if isinstance(data, Mapping):
                payloads.append(data)
    canonical = check_canonical_consistency(api_name, normalized, payloads)

    # Prefer the raw response carrying the canonical payload; otherwise (all
    # empty, or all failures) fall back to the first observed response.
    for status, body in responses:
        if isinstance(body, Mapping) and body.get("data") is canonical:
            return status, body
    return responses[0]


# ---------------------------------------------------------------------------
# Fixture replay
# ---------------------------------------------------------------------------
def fixture_key(api_name: str, params: Mapping[str, object]) -> RequestKey:
    """The identity of one logical request: ``(api_name, sorted params)``.

    Sorting makes the key order-independent, so a caller may pass keyword
    parameters in any order and still match the recording.
    """
    return (
        api_name,
        tuple(sorted(_coerce_params(params).items())),
    )


def _normalize_recorded(value: object) -> "list[tuple[int, object]]":
    """Accept either a single ``(status, body)`` pair or a sequence of them."""
    if (
        isinstance(value, tuple)
        and len(value) == 2
        and isinstance(value[0], int)
    ):
        return [value]  # type: ignore[list-item]
    return list(value)  # type: ignore[arg-type]


def replay_transport(
    recordings: Mapping[RequestKey, object],
    *,
    repeat_last: bool = True,
) -> Transport:
    """Build a deterministic, network-free :data:`Transport` from recordings.

    ``recordings`` maps a :func:`fixture_key` to either one
    ``(status, body)`` response or an ordered sequence of them. Responses for
    a key are consumed in order, which is what lets a test replay a real
    failure-then-success retry sequence. Once a key's sequence is exhausted,
    ``repeat_last=True`` (the default) serves its final response again --
    convenient for an all-failure fixture and identical in spirit to serving
    one recording for a request, while ``repeat_last=False`` raises instead.

    A request with no recording raises :class:`KeyError` naming the request
    and every recorded key -- never a fabricated or silently-empty response.

    This is the one reusable fixture-replay mechanism every later Phase 4D-B
    task imports; no other task should reimplement it.
    """
    queues = {key: _normalize_recorded(value) for key, value in recordings.items()}
    last_served: dict[object, tuple[int, object]] = {}

    def transport(api_name: str, params: Mapping[str, str]) -> tuple[int, object]:
        key = fixture_key(api_name, params)
        if key not in queues:
            raise KeyError(
                f"No recorded Tushare response for api_name={api_name!r} "
                f"params={dict(params)!r}; recorded keys: "
                f"{sorted(queues)!r}"
            )
        queue = queues[key]
        if queue:
            response = queue.pop(0)
            last_served[key] = response
            return response
        if repeat_last and key in last_served:
            return last_served[key]
        raise KeyError(
            f"Recorded Tushare responses for api_name={api_name!r} "
            f"params={dict(params)!r} are exhausted; pass repeat_last=True "
            "or record more responses."
        )

    return transport


# ---------------------------------------------------------------------------
# The concrete proxy client
# ---------------------------------------------------------------------------
class ProxyTushareClient:
    """A :class:`TushareClient` backed by the third-party Tushare proxy.

    Authentication is read from the ``token_env_var`` environment variable
    (default ``TUSHARE_PROXY_TOKEN``) **at call time**, so a client can be
    constructed in an environment where the token is absent and used with an
    injected :func:`replay_transport` for fully offline tests.

    ``transport`` is the test seam: when injected, no network is touched, no
    token is required, and ``fetch`` still applies the full retry/empty
    policy on top of whatever the transport returns. When omitted, the live
    HTTPS transport is used and a missing token raises before any request.

    ``sleep`` is injected for tests; the default is :func:`time.sleep`, and
    backoff delays are ``min(base_backoff * 2**(attempt-1), max_backoff)``.
    """

    def __init__(
        self,
        base_url: str = PROXY_BASE_URL,
        token_env_var: str = TOKEN_ENV_VAR,
        *,
        transport: Transport | None = None,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        base_backoff: float = DEFAULT_BASE_BACKOFF_SECONDS,
        max_backoff: float = DEFAULT_MAX_BACKOFF_SECONDS,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        if base_backoff < 0 or max_backoff < 0:
            raise ValueError("backoff delays must be >= 0")
        self._base_url = base_url.rstrip("/")
        self._token_env_var = token_env_var
        self._is_live = transport is None
        self._transport: Transport = (
            transport if transport is not None else self._live_transport
        )
        self._max_attempts = max_attempts
        self._base_backoff = base_backoff
        self._max_backoff = max_backoff
        self._sleep = sleep

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def is_live(self) -> bool:
        """True when this client will make real network requests."""
        return self._is_live

    # -- the protocol method ------------------------------------------------
    def fetch(
        self,
        api_name: str,
        *,
        retry_on_empty: bool = False,
        **params: str,
    ) -> dict:
        request_params = _coerce_params(params)

        # Read the token at call time. With an injected transport no token is
        # needed at all (offline replay is a first-class mode).
        if self._is_live and not os.environ.get(self._token_env_var):
            raise TushareAPIError(
                api_name,
                request_params,
                "No Tushare proxy token available: set the "
                f"{self._token_env_var} environment variable, or inject a "
                "transport (e.g. replay_transport) for offline use.",
                error="missing_token",
            )

        last_status: int | None = None
        last_body: object = None
        for attempt in range(1, self._max_attempts + 1):
            status, body = self._transport(api_name, request_params)
            last_status, last_body = status, body

            if _is_retryable(status, body):
                if attempt < self._max_attempts:
                    self._sleep(self._backoff_delay(attempt))
                    continue
                raise TushareAPIError(
                    api_name,
                    request_params,
                    _error_message(api_name, request_params, status, body)
                    + f" (exhausted {self._max_attempts} attempts)",
                    status_code=status,
                    error=_error_code(body),
                    body=body,
                )

            # Non-retryable non-200: the proxy's own rejection (e.g. its
            # date-range cap). Never retried, and the body/message is kept
            # intact so the constraint is visible to the caller.
            if status != 200:
                raise TushareAPIError(
                    api_name,
                    request_params,
                    _error_message(api_name, request_params, status, body),
                    status_code=status,
                    error=_error_code(body),
                    body=body,
                )

            data = self._extract_data(api_name, request_params, status, body)

            if retry_on_empty and not _payload_items(data):
                if attempt < self._max_attempts:
                    self._sleep(self._backoff_delay(attempt))
                    continue
                raise TushareEmptyResponseError(
                    api_name,
                    request_params,
                    "Tushare request "
                    f"api_name={api_name!r} params={request_params!r} returned "
                    f"an empty payload after {self._max_attempts} attempts "
                    "(retry_on_empty=True).",
                    status_code=status,
                    body=body,
                )

            return data

        # Only reachable if max_attempts was mutated; defensive.
        raise TushareAPIError(
            api_name,
            request_params,
            f"Tushare request api_name={api_name!r} params={request_params!r} "
            "did not complete",
            status_code=last_status,
            error=_error_code(last_body),
            body=last_body,
        )

    # -- recording helper ---------------------------------------------------
    def record(
        self, api_name: str, *, samples: int = 3, **params: str
    ) -> tuple[int, object]:
        """Recording-time helper: fetch ``api_name`` ``samples`` times and
        return the canonical raw response, enforcing canonical consistency.

        See :func:`record_fixture`. This is a convenience passthrough over the
        client's own transport; it is never used during replay-driven tests.
        """
        return record_fixture(
            self._transport, api_name, samples=samples, **params
        )

    # -- internals ----------------------------------------------------------
    def _backoff_delay(self, attempt: int) -> float:
        return min(
            self._base_backoff * (2 ** (attempt - 1)), self._max_backoff
        )

    @staticmethod
    def _extract_data(
        api_name: str,
        params: Mapping[str, str],
        status: int,
        body: object,
    ) -> dict:
        """Pull ``body['data']`` out of a 200 response.

        The proxy's success envelope is ``{"code": 0, "data": {...}}``. A
        non-zero ``code`` is an error even on HTTP 200. A body that already
        carries ``fields``/``items`` at the top level is accepted too, for
        resilience against envelope variation -- this does not change the
        ``TushareClient`` contract, which is always the ``fields``/``items``
        payload.
        """
        if isinstance(body, Mapping):
            code = body.get("code")
            if code not in (0, None):
                raise TushareAPIError(
                    api_name,
                    params,
                    _error_message(api_name, params, status, body),
                    status_code=status,
                    error=_error_code(body),
                    body=body,
                )
            data = body.get("data")
            if isinstance(data, Mapping):
                return dict(data)
            if "fields" in body or "items" in body:
                return {
                    "fields": body.get("fields", []),
                    "items": body.get("items", []),
                }
        raise TushareAPIError(
            api_name,
            params,
            f"Tushare request api_name={api_name!r} params={dict(params)!r} "
            f"returned status {status} with no usable data payload: {body!r}",
            status_code=status,
            error="malformed_response",
            body=body,
        )

    def _resolve_token(self) -> str | None:
        """Read the token from the environment *now* -- never at import or
        construction time, and never stored on the instance."""
        return os.environ.get(self._token_env_var)

    def _live_transport(
        self, api_name: str, params: Mapping[str, str]
    ) -> tuple[int, object]:
        """Real HTTPS POST to ``{base_url}/{api_name}``.

        The token travels only in the ``X-API-Key`` header -- never in the
        URL, the body, or any log/exception message. A non-2xx response is
        captured from ``HTTPError`` rather than propagated, so the retry
        policy in :meth:`fetch` remains the single decision point.
        """
        token = self._resolve_token()
        url = f"{self._base_url}/{urllib.parse.quote(api_name)}"
        # The proxy's OpenAPI exposes ``POST /tushare/pro/{api_name}`` with
        # api_name in the path and no documented request body, so the body is
        # the flat parameter object (matching the Tushare SDK's
        # ``pro.query(api_name, **params)``). Replay-based tests do not depend
        # on this; running the fixture recorder with a real token confirms it.
        payload = json.dumps(dict(params)).encode("utf-8")
        request = urllib.request.Request(url, data=payload, method="POST")
        request.add_header("Content-Type", "application/json")
        if token:
            request.add_header("X-API-Key", token)

        try:
            with urllib.request.urlopen(request) as response:
                status = response.status
                raw = response.read()
        except urllib.error.HTTPError as exc:
            status = exc.code
            raw = exc.read()

        return status, _decode_body(raw)


# ---------------------------------------------------------------------------
# Fixture-capture entry point
# ---------------------------------------------------------------------------
def _sanitize_filename(api_name: str, params: Mapping[str, str]) -> str:
    stem = "_".join(
        [api_name] + [f"{key}-{value}" for key, value in sorted(params.items())]
    )
    return "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in stem)


def _parse_request(spec: str) -> "tuple[str, dict[str, str]]":
    tokens = spec.split()
    if not tokens:
        raise ValueError("empty request spec")
    api_name, *pairs = tokens
    params: dict[str, str] = {}
    for pair in pairs:
        if "=" not in pair:
            raise ValueError(f"expected key=value, got {pair!r}")
        key, value = pair.split("=", 1)
        params[key] = value
    return api_name, params


def main(argv: "list[str] | None" = None) -> int:
    """Live fixture-capture script (recording-time only).

    Run with a real token in the environment::

        TUSHARE_PROXY_TOKEN=... .venv/bin/python -m \\
            smart_beta.vendors.tushare.proxy_client \\
            --out tests/fixtures/tushare/client \\
            "daily ts_code=000001.SZ start_date=20230101 end_date=20230115" \\
            "adj_factor ts_code=000001.SZ trade_date=20230113"

    Each request is issued ``--samples`` times; non-empty payloads must agree
    byte-for-byte or :class:`TushareNonDeterministicResponseError` is raised
    and nothing is written for that request. Response bodies are written
    verbatim (the token never appears in any of them) alongside a manifest
    entry keyed by ``(api_name, sorted params)``.
    """
    parser = ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out", type=Path, required=True, help="fixture output directory"
    )
    parser.add_argument(
        "--base-url", default=PROXY_BASE_URL, help="proxy base URL"
    )
    parser.add_argument(
        "--samples", type=int, default=3, help="repeat count per request"
    )
    parser.add_argument(
        "requests",
        nargs="+",
        help='one or more "api_name key=value key=value" specs',
    )
    args = parser.parse_args(argv)

    if not os.environ.get(TOKEN_ENV_VAR):
        print(
            f"error: {TOKEN_ENV_VAR} is not set. This recorder needs the real "
            "proxy token; it is read from the environment and never written "
            "to disk.",
        )
        return 2

    client = ProxyTushareClient(base_url=args.base_url)
    args.out.mkdir(parents=True, exist_ok=True)
    manifest_path = args.out / "manifest.json"
    manifest: dict[str, object] = {}
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text())
    recordings = manifest.setdefault("recordings", {})
    assert isinstance(recordings, dict)

    for spec in args.requests:
        api_name, params = _parse_request(spec)
        print(f"POST {args.base_url}/{api_name} {params}", flush=True)
        status, body = client.record(api_name, samples=args.samples, **params)
        filename = _sanitize_filename(api_name, params) + ".json"
        (args.out / filename).write_text(json.dumps(body, indent=2) + "\n")
        recordings[filename] = {
            "api_name": api_name,
            "params": params,
            "status_code": status,
        }
        print(f"  status {status} -> {filename}", flush=True)

    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"wrote {len(args.requests)} recording(s) to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
