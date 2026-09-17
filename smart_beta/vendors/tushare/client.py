"""Transport-neutral Tushare client contract (Phase 4D-B, P4DB-1).

This module is the *only* interface every other Phase 4D-B module is allowed
to depend on when it needs raw Tushare data. It defines:

* the :class:`TushareClient` :class:`typing.Protocol` -- a single
  ``fetch(api_name, **params)`` method returning Tushare's raw
  ``{"fields": [...], "items": [[...], ...]}`` payload;
* the shared exception hierarchy a caller may catch
  (:class:`TushareAPIError` and its two distinguishable subclasses);
* the :data:`Transport` callable type a concrete client is built on.

Nothing here knows how the data is actually reached. A proxy-backed client, a
direct official-API client, or an offline fixture-replay transport are all
equally valid implementations of :class:`TushareClient`; swapping one for
another must not require a caller change. Transport-specific behavior
(retries, backoff, status-code handling, authentication headers, response
envelopes) lives entirely in the concrete implementation, never here and
never in a caller.

This module performs no I/O and reads no environment variables.
"""

from __future__ import annotations

from typing import Callable, Mapping, Protocol, runtime_checkable

#: The lowest-level seam a concrete client is built on:
#: ``(api_name, params) -> (http_status_code, parsed_json_body)``.
#:
#: A transport always returns the raw ``(status, body)`` pair -- it never
#: raises for an HTTP error status, because deciding what a non-200 status
#: means is the concrete client's job (and what makes an offline replay
#: transport indistinguishable from a live one to the rest of the system).
Transport = Callable[[str, Mapping[str, str]], "tuple[int, object]"]

__all__ = [
    "Transport",
    "TushareClient",
    "TushareAPIError",
    "TushareEmptyResponseError",
    "TushareNonDeterministicResponseError",
]


class TushareAPIError(Exception):
    """Base failure type for any Tushare request that did not yield data.

    Carries enough context to diagnose and to drive retry decisions without
    re-issuing the call: the ``api_name`` and ``params`` sent, the observed
    ``status_code`` (when there was one), a machine-readable ``error`` code
    (when the response body supplied one), and the parsed ``body``.

    ``error`` is the hook that lets a caller distinguish a specific,
    non-retryable rejection -- for example a stability constraint the API
    imposes on a request's parameters -- from a transient transport failure,
    without string-matching the human-readable message. The original
    message text from the response body is always preserved verbatim in
    ``str(error)``.

    Secrets are never included: this type only ever receives API parameters
    and response bodies, never authentication material.
    """

    def __init__(
        self,
        api_name: str,
        params: Mapping[str, str],
        message: str,
        *,
        status_code: int | None = None,
        error: str | None = None,
        body: object = None,
    ) -> None:
        self.api_name = api_name
        self.params = dict(params)
        self.status_code = status_code
        self.error = error
        self.body = body
        super().__init__(message)


class TushareEmptyResponseError(TushareAPIError):
    """Raised when a request that a caller declared should be non-empty
    (opt-in ``retry_on_empty=True``) still came back empty after every retry.

    This is deliberately loud rather than a silent empty payload: without
    caller context a client cannot tell "this security genuinely has no rows
    for this period" from "the request transiently returned nothing", so the
    empty case is never upgraded to a normal result on the caller's behalf.
    """

    def __init__(
        self,
        api_name: str,
        params: Mapping[str, str],
        message: str,
        *,
        status_code: int | None = None,
        error: str | None = "empty_response",
        body: object = None,
    ) -> None:
        super().__init__(
            api_name,
            params,
            message,
            status_code=status_code,
            error=error,
            body=body,
        )


class TushareNonDeterministicResponseError(TushareAPIError):
    """Raised at fixture-recording time when the same request produced two
    different non-empty payloads.

    Recording must never silently pick one of several conflicting answers for
    an identical request; the conflict itself is the finding. This type names
    the request and every differing payload it observed.
    """

    def __init__(
        self,
        api_name: str,
        params: Mapping[str, str],
        payloads: "list[object]",
    ) -> None:
        self.payloads = list(payloads)
        message = (
            f"Non-deterministic Tushare response for api_name={api_name!r} "
            f"params={dict(params)!r}: {len(self.payloads)} distinct "
            f"non-empty payloads were observed for the same request: "
            f"{self.payloads!r}"
        )
        super().__init__(
            api_name,
            params,
            message,
            error="non_deterministic_response",
            body=self.payloads,
        )


@runtime_checkable
class TushareClient(Protocol):
    """The one raw-data interface the rest of Phase 4D-B depends on.

    ``fetch`` returns Tushare's own payload shape --
    ``{"fields": [...], "items": [[...], ...]}`` -- with no PIT mapping, no
    schema validation, no knowledge-date parsing, and no identifier
    resolution. Those are later tasks' jobs.

    ``retry_on_empty`` is an explicit opt-in for callers that already know a
    particular query should return rows: it asks the implementation to retry
    before finally raising :class:`TushareEmptyResponseError`. It defaults to
    ``False`` because a client cannot, on its own, distinguish a genuinely
    empty result from a transiently empty one.
    """

    def fetch(
        self,
        api_name: str,
        *,
        retry_on_empty: bool = False,
        **params: str,
    ) -> dict:
        """Fetch one raw Tushare response.

        Returns the response's ``data`` payload as ``{"fields": [...],
        "items": [[...], ...]}`` -- or raises :class:`TushareAPIError` (or a
        subclass). No transport detail beyond that contract is promised.
        """
        ...
