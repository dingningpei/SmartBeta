"""Raw Tiingo REST client (Phase 4B, P4B-1).

This is the lowest layer of the Tiingo adapter and the only place in
Phase 4B that is allowed to talk to the network. It is deliberately thin:
each of its four public data methods fetches one Tiingo endpoint and returns
Tiingo's **native** JSON shape verbatim -- no PIT mapping, no schema
validation, no corporate-action adjustment, no identifier resolution. Those
are every later Phase 4B task's job.

Design notes
------------
* **No new dependency.** The live transport is a hand-rolled
  ``urllib.request`` GET; ``pyproject.toml`` is untouched (no ``requests``).
* **Injectability.** Every data method routes through
  ``self._transport(path, params) -> (status_code, parsed_body)``. Tests
  inject :func:`replay_transport` (recorded fixtures) or an inline spy, and
  therefore never touch the network. The live transport is used only when no
  transport is injected, and only in that case is an API key required.
* **Errors are typed.** A non-200 status becomes :class:`TiingoAPIError` in
  ``_call`` -- never in the transport, which always returns the raw
  ``(status, body)`` pair. This keeps the transport a pure function of the
  request and makes the recorded RGEN 400 specimen replayable.
* **Auth.** Verified against live calls via
  ``scripts/fetch_tiingo_fixtures.py``. Tiingo accepts
  ``Authorization: Token <key>``; the live transport uses that header (not
  a ``token`` query parameter) so the secret never appears in the URL. A
  missing key with no injected transport still raises
  :class:`TiingoConfigError` before any request is made.
* **Daily metadata has no ``permaTicker``.** Real captured
  ``GET /tiingo/daily/{ticker}`` bodies for AAPL and delisted TWTR contain
  ``ticker``, ``name``, ``exchangeCode``, ``startDate``, ``endDate``, and
  ``description`` only. This client does not invent a permanent-identity
  field; P4B-2 must not assume one is present on this endpoint.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable, Mapping

#: ``(path, query_params) -> (http_status_code, parsed_json_body)``.
Transport = Callable[[str, Mapping[str, str]], "tuple[int, object]"]

BASE_URL = "https://api.tiingo.com"
API_KEY_ENV_VAR = "TIINGO_API_KEY"


class TiingoConfigError(Exception):
    """Raised at construction when no API key is available anywhere
    (neither passed explicitly nor via the ``TIINGO_API_KEY`` environment
    variable) and no test transport was injected either."""


class TiingoAPIError(Exception):
    """Raised when a call's HTTP status is not 200.

    Carries enough to diagnose without re-issuing the call: the request
    path, the exact query params sent, the status code, and the parsed (or
    raw-text, if unparseable) response body.
    """

    def __init__(
        self,
        path: str,
        params: Mapping[str, str],
        status_code: int,
        body: object,
    ) -> None:
        self.path = path
        self.params = dict(params)
        self.status_code = status_code
        self.body = body
        super().__init__(
            f"Tiingo request to {path} with params={dict(params)} "
            f"returned status {status_code}: {body!r}"
        )


class TiingoClient:
    """Thin wrapper over Tiingo's REST API.

    Returns Tiingo-native JSON shapes verbatim -- no PIT semantics, no
    schema mapping, no adjustment. That is every later Phase 4B task's job,
    not this client's.
    """

    def __init__(
        self,
        api_key: str | None = None,
        transport: Transport | None = None,
    ) -> None:
        """``api_key`` falls back to the ``TIINGO_API_KEY`` environment
        variable. If neither is set AND no ``transport`` is injected, raise
        :class:`TiingoConfigError` immediately -- never silently send a
        keyless request. When ``transport`` *is* injected (the fixture-replay
        path every test uses), a missing key is fine: the transport never
        touches the network.
        """
        resolved_key = (
            api_key if api_key is not None else os.environ.get(API_KEY_ENV_VAR)
        )
        if transport is None and not resolved_key:
            raise TiingoConfigError(
                "No Tiingo API key available: pass api_key explicitly, set "
                f"the {API_KEY_ENV_VAR} environment variable, or inject a "
                "transport (e.g. replay_transport) for offline use."
            )
        self._api_key = resolved_key
        self._transport: Transport = (
            transport if transport is not None else self._live_transport
        )

    # ------------------------------------------------------------------
    # Public data methods (Tiingo-native shapes only)
    # ------------------------------------------------------------------
    def get_eod_prices(self, ticker: str, start_date: str, end_date: str) -> list[dict]:
        """Daily EOD prices for ``ticker`` over ``[start_date, end_date]``.

        Endpoint: ``GET /tiingo/daily/{ticker}/prices`` with
        ``startDate``/``endDate`` ISO strings. Returns the parsed JSON array
        of per-day dicts verbatim (``date, open, high, low, close, volume,
        adjOpen, adjHigh, adjLow, adjClose, adjVolume, divCash,
        splitFactor``) -- no field is renamed, reshaped, or dropped.
        """
        path = f"/tiingo/daily/{urllib.parse.quote(ticker)}/prices"
        params = {"startDate": start_date, "endDate": end_date}
        return self._call(path, params)

    def get_meta(self, ticker: str) -> dict:
        """Security metadata for ``ticker``.

        Endpoint: ``GET /tiingo/daily/{ticker}``. Returns the parsed JSON
        object verbatim. Live AAPL and TWTR captures contain ``ticker``,
        ``name``, ``exchangeCode``, ``startDate``, ``endDate``, and
        ``description`` -- and neither contains ``permaTicker`` (or any
        other permanent-identity field). This method does not invent one;
        deciding ``stock_id`` is P4B-2's job.
        """
        path = f"/tiingo/daily/{urllib.parse.quote(ticker)}"
        return self._call(path, {})

    def get_fundamentals_asreported(
        self,
        ticker: str,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> list[dict]:
        """As-reported fundamentals statements for ``ticker``.

        Endpoint: ``GET /tiingo/fundamentals/{ticker}/statements`` called
        with ``asReported=true``. Returns the parsed JSON array of
        per-statement dicts verbatim. A live AAPL capture has top-level
        ``date``, ``year``, ``quarter``, and ``statementData``.
        """
        path = f"/tiingo/fundamentals/{urllib.parse.quote(ticker)}/statements"
        params = self._fundamentals_params(start_date, end_date)
        params["asReported"] = "true"
        return self._call(path, params)

    def get_fundamentals_normalized(
        self,
        ticker: str,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> list[dict]:
        """Default (normalized) fundamentals statements for ``ticker``.

        The *same* endpoint family as :meth:`get_fundamentals_asreported`,
        called **without** ``asReported=true``. P4B-6 uses this response's
        ``date`` field only, as report-period-end metadata -- never its
        ``statementData`` values as canonical facts. This client method just
        fetches; it has no opinion about how its result is used downstream.
        """
        path = f"/tiingo/fundamentals/{urllib.parse.quote(ticker)}/statements"
        params = self._fundamentals_params(start_date, end_date)
        return self._call(path, params)

    # ------------------------------------------------------------------
    # Internal glue
    # ------------------------------------------------------------------
    @staticmethod
    def _fundamentals_params(
        start_date: str | None, end_date: str | None
    ) -> dict[str, str]:
        params: dict[str, str] = {}
        if start_date is not None:
            params["startDate"] = start_date
        if end_date is not None:
            params["endDate"] = end_date
        return params

    def _call(self, path: str, params: Mapping[str, str]) -> object:
        """Run one request through the transport and turn a non-200 status
        into :class:`TiingoAPIError`. The transport itself never raises for
        an HTTP error status.
        """
        status_code, body = self._transport(path, params)
        if status_code != 200:
            raise TiingoAPIError(path, params, status_code, body)
        return body

    def _live_transport(
        self, path: str, params: Mapping[str, str]
    ) -> tuple[int, object]:
        """Real HTTPS GET via ``urllib.request``. Used only when no transport
        was injected (and therefore only when an API key is present).

        Always returns ``(status_code, body)``; a non-2xx status is captured
        from ``HTTPError`` rather than propagated, so ``_call`` remains the
        single place that decides what a non-200 means. The body is parsed as
        JSON when possible, and otherwise returned as decoded text so an
        error can still be inspected.
        """
        url = BASE_URL + path
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"

        request = urllib.request.Request(url, method="GET")
        request.add_header("Authorization", f"Token {self._api_key}")

        try:
            with urllib.request.urlopen(request) as response:
                status_code = response.status
                raw = response.read()
        except urllib.error.HTTPError as exc:
            status_code = exc.code
            raw = exc.read()

        return status_code, _decode_body(raw)


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


def replay_transport(
    recordings: Mapping[str, "tuple[int, object]"],
) -> Transport:
    """Build a deterministic, network-free :data:`Transport` from recorded
    ``{request_path: (status_code, parsed_body)}`` specimens.

    Query params are deliberately **not** part of the lookup key: one fixture
    scenario exercises exactly one endpoint/ticker combination, so the path
    alone is unambiguous in practice (and it lets the same recording satisfy
    a call with or without optional date params).

    Raises :class:`KeyError` naming the unmatched path when a caller asks for
    an endpoint that was not recorded -- never a silently-empty or fabricated
    response.

    This is the *one* reusable fixture-replay mechanism every later Phase 4B
    task's tests import; no other task should reimplement it.
    """

    def transport(path: str, params: Mapping[str, str]) -> tuple[int, object]:
        try:
            return recordings[path]
        except KeyError:
            raise KeyError(
                f"No recorded Tiingo response for path {path!r} "
                f"(params={dict(params)!r}); recorded paths: "
                f"{sorted(recordings)!r}"
            ) from None

    return transport
