"""G6 — SEC EDGAR document fetcher bound to the hash-verified fetch plan.

This is the network-facing half of the gate-6 audit (spec section 10).  It
opens no socket by itself: every request goes through an injected transport,
so the whole module is exercised offline with a fake transport in tests.  It
never reads a provider credential and never writes the SEC contact to a log,
an artifact or Git.

The fetch-plan serialization and hash live in exactly one place,
:func:`smart_beta.dataset_v1.gate6_audit.fetch_plan_sha256`; the fetcher
imports and verifies that hash rather than re-deriving its own.

The retriever is deliberately narrow:

* only ``GET https://www.sec.gov/Archives/<Filename>`` for a Filename present
  in the fetch plan is allowed;
* the SEC fair-access ``User-Agent`` contact comes from ``SEC_CONTACT`` and
  must be present, else nothing is fetched;
* at most 4 requests per second, at most 1 GET per planned filing plus
  retries, and a hard cap of 2,500 requests;
* retries only HTTP 429, HTTP 5xx and timeouts (twice), never 403, 404 or a
  generic transport error;
* atomic, SHA-256-addressed caching, so a resumed run skips verified files.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from smart_beta.dataset_v1 import gate6_audit as _audit

REQUEST_CAP = 2500
BACKOFFS = (2.0, 8.0)
USER_AGENT_PREFIX = "SmartBeta-Gate6-Audit"
SEC_HOST = "www.sec.gov"
SEC_PATH_PREFIX = "/Archives/edgar/data/"


def is_retry_status(status: int | None) -> bool:
    """Every HTTP 5xx and HTTP 429 is retryable; nothing else is."""
    if status is None:
        return False
    return status == 429 or 500 <= status <= 599


def _is_timeout(error: BaseException) -> bool:
    if isinstance(error, TimeoutError):
        return True
    reason = getattr(error, "reason", None)
    return isinstance(reason, TimeoutError)

CREDENTIAL_VARIABLES = (
    "TIINGO_API_KEY",
    "TUSHARE_PROXY_TOKEN",
    "TUSHARE_BASIC_PROXY_TOKEN",
    "TUSHARE_API_TOKEN",
    "DEEPSEEK_API_KEY",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "OPENAI_API_KEY",
)


class FetchError(Exception):
    """Base class for every SEC retrieval error."""


class FetchPlanMismatch(FetchError, ValueError):
    """The fetch plan does not match its bound hash, or a filing is off-plan."""


class ContactMissing(FetchError, ValueError):
    """``SEC_CONTACT`` is required and was not supplied."""


class UrlRefused(FetchError, ValueError):
    """A URL is outside the exact host/path/plan allowlist."""


@dataclass(frozen=True)
class TransportResponse:
    status: int | None
    body: bytes = b""
    error: str | None = None


@dataclass(frozen=True)
class RetrievalRecord:
    url: str
    status: int | None
    bytes: int
    sha256: str | None
    attempt: int
    utc: str
    outcome: str


@dataclass(frozen=True)
class FetchOutcome:
    specimen_id: str
    accession: str
    outcome: str
    cached_path: str | None = None
    doc_sha256: str | None = None


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build_url(filename: str) -> str:
    if filename.startswith("/Archives/"):
        return f"https://{SEC_HOST}{filename}"
    if filename.startswith("/"):
        filename = filename[1:]
    return f"https://{SEC_HOST}/Archives/{filename}"


class SecFetcher:
    """A fail-closed, cache-backed SEC document retriever."""

    def __init__(
        self,
        plan: Sequence[Any],
        *,
        plan_sha256: str,
        transport: Callable[[str, Mapping[str, str]], TransportResponse],
        contact: str | None = None,
        cache_dir: str | Path | None = None,
        cache_index: Mapping[str, str] | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        max_requests: int = REQUEST_CAP,
        max_rate: float = 4.0,
        initial_requests: int = 0,
    ) -> None:
        self._plan = list(plan)
        actual = _audit.fetch_plan_sha256(self._plan)
        if actual != plan_sha256:
            raise FetchPlanMismatch(
                f"fetch-plan hash {actual} != bound {plan_sha256}"
            )
        self._bound_plan_sha256 = plan_sha256
        self._mapped = [
            row for row in self._plan if row.mapping_state == "MAPPED"
        ]
        self._allowed = {row.filename for row in self._mapped}
        self._transport = transport
        self._contact = (
            contact if contact is not None else os.environ.get("SEC_CONTACT")
        )
        if not self._contact:
            raise ContactMissing("SEC_CONTACT is required for SEC retrieval")
        self._cache_dir = Path(cache_dir) if cache_dir is not None else None
        self._cache_index = dict(cache_index or {})
        self._clock = clock
        self._sleep = sleep
        self._max_requests = max_requests
        self._min_interval = 1.0 / max_rate if max_rate > 0 else 0.0
        self._last_request: float | None = None
        self._requests = int(initial_requests)
        self._log: list[RetrievalRecord] = []
        self._retrieved: dict[str, str] = {}
        self._used_cache: dict[str, str] = {}

    # -- introspection -----------------------------------------------------

    @property
    def contact(self) -> str:
        return str(self._contact)

    @property
    def requests(self) -> int:
        return self._requests

    @property
    def log(self) -> list[RetrievalRecord]:
        return list(self._log)

    def _user_agent(self) -> str:
        return f"{USER_AGENT_PREFIX} {self._contact}"

    # -- plan and allowlist ------------------------------------------------

    def _check_plan(self) -> None:
        actual = _audit.fetch_plan_sha256(self._plan)
        if actual != self._bound_plan_sha256:
            raise FetchPlanMismatch("fetch-plan hash changed after construction")

    def _check_url(self, filename: str) -> str:
        if filename not in self._allowed:
            raise UrlRefused(f"filename {filename!r} is not in the fetch plan")
        url = build_url(filename)
        if not url.startswith(f"https://{SEC_HOST}{SEC_PATH_PREFIX}"):
            raise UrlRefused(f"url {url!r} is outside the SEC Archives prefix")
        return url

    # -- rate limiting -----------------------------------------------------

    def _throttle(self) -> None:
        now = self._clock()
        if self._last_request is not None:
            elapsed = now - self._last_request
            if elapsed < self._min_interval:
                self._sleep(self._min_interval - elapsed)
                now = self._clock()
        self._last_request = now

    # -- transport ---------------------------------------------------------

    def _cap_reached(self) -> bool:
        return self._requests >= self._max_requests

    def _request(self, url: str, *, attempt: int) -> TransportResponse:
        if self._cap_reached():
            return TransportResponse(status=None, error="REQUEST_CAP_REACHED")
        self._throttle()
        self._requests += 1
        try:
            return self._transport(url, {"User-Agent": self._user_agent()})
        except TimeoutError:
            return TransportResponse(status=None, error="TIMEOUT")
        except OSError as exc:  # generic transport error: never retried
            if _is_timeout(exc):
                return TransportResponse(status=None, error="TIMEOUT")
            return TransportResponse(status=None, error=f"TRANSPORT:{exc}")

    def _record(
        self,
        url: str,
        response: TransportResponse,
        *,
        attempt: int,
        outcome: str,
    ) -> None:
        body = response.body or b""
        utc = datetime.now(timezone.utc).isoformat()
        digest = _sha256_bytes(body) if response.status == 200 else None
        self._log.append(
            RetrievalRecord(
                url=url,
                status=response.status,
                bytes=len(body),
                sha256=digest,
                attempt=attempt,
                utc=utc,
                outcome=outcome,
            )
        )

    # -- caching -----------------------------------------------------------

    def _cache_path(self, accession: str) -> Path | None:
        if self._cache_dir is None:
            return None
        return self._cache_dir / "evidence" / f"{accession}.txt"

    def _cached_verified(self, accession: str) -> str | None:
        path = self._cache_path(accession)
        if path is None or not path.exists():
            return None
        expected = self._cache_index.get(accession)
        if expected is None:
            return None
        actual = _sha256_bytes(path.read_bytes())
        if actual == expected:
            return actual
        return None

    def _write_cache(self, accession: str, body: bytes) -> tuple[str, str | None]:
        digest = _sha256_bytes(body)
        path = self._cache_path(accession)
        if path is None:
            return digest, None
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(body)
        tmp.replace(path)
        return digest, str(path)

    # -- per-filing fetch --------------------------------------------------

    def fetch_filing(self, filing: Any) -> FetchOutcome:
        self._check_plan()
        url = self._check_url(filing.filename)
        cached = self._cached_verified(filing.accession)
        if cached is not None:
            self._used_cache[filing.accession] = cached
            return FetchOutcome(
                specimen_id=filing.specimen_id,
                accession=filing.accession,
                outcome="CACHED",
                cached_path=str(self._cache_path(filing.accession)),
                doc_sha256=cached,
            )
        if self._cap_reached():
            return FetchOutcome(
                specimen_id=filing.specimen_id,
                accession=filing.accession,
                outcome="REQUEST_CAP_REACHED",
            )

        attempt = 0
        while True:
            attempt += 1
            response = self._request(url, attempt=attempt)
            if response.error == "REQUEST_CAP_REACHED":
                return FetchOutcome(
                    specimen_id=filing.specimen_id,
                    accession=filing.accession,
                    outcome="REQUEST_CAP_REACHED",
                )
            status = response.status
            if status == 200:
                digest, path = self._write_cache(
                    filing.accession, response.body or b""
                )
                self._record(url, response, attempt=attempt, outcome="OK")
                self._retrieved[filing.accession] = digest
                return FetchOutcome(
                    specimen_id=filing.specimen_id,
                    accession=filing.accession,
                    outcome="FETCHED",
                    cached_path=path,
                    doc_sha256=digest,
                )
            # Only HTTP 429, HTTP 5xx and timeouts are retried.
            retryable = (
                is_retry_status(status) or response.error == "TIMEOUT"
            )
            if not retryable or attempt > len(BACKOFFS):
                self._record(
                    url, response, attempt=attempt, outcome="UNRETRIEVABLE"
                )
                return FetchOutcome(
                    specimen_id=filing.specimen_id,
                    accession=filing.accession,
                    outcome="UNRETRIEVABLE",
                )
            self._record(url, response, attempt=attempt, outcome="RETRY")
            self._sleep(BACKOFFS[attempt - 1])

    def fetch_all(self) -> list[FetchOutcome]:
        """Fetch each distinct planned file at most once per run.

        Several fetch-plan rows (different specimens) may share the same
        filename/accession.  The first row performs the retrieval; any later
        row with the same filename reuses that result (``CACHED`` with the
        same ``doc_sha256`` when the first retrieval succeeded).
        """
        results: list[FetchOutcome] = []
        seen: dict[str, FetchOutcome] = {}
        for row in self._mapped:
            key = row.filename
            if key in seen:
                first = seen[key]
                reused = (
                    "CACHED"
                    if first.outcome in ("FETCHED", "CACHED")
                    else first.outcome
                )
                results.append(
                    FetchOutcome(
                        specimen_id=row.specimen_id,
                        accession=row.accession,
                        outcome=reused,
                        cached_path=first.cached_path,
                        doc_sha256=first.doc_sha256,
                    )
                )
                continue
            outcome = self.fetch_filing(row)
            seen[key] = outcome
            results.append(outcome)
        return results

    def log_jsonl(self) -> str:
        """The ``retrieval_log.jsonl`` payload (no contact, no secret)."""
        return "".join(
            json.dumps(
                {
                    "url": record.url,
                    "status": record.status,
                    "bytes": record.bytes,
                    "sha256": record.sha256,
                    "attempt": record.attempt,
                    "utc": record.utc,
                    "outcome": record.outcome,
                },
                sort_keys=True,
            )
            + "\n"
            for record in self._log
        )


def urllib_transport(
    url: str,
    headers: Mapping[str, str],
    *,
    timeout: float = 30.0,
) -> TransportResponse:
    """The real E2 transport; used only by the authorized network step."""
    import urllib.error
    import urllib.request

    request = urllib.request.Request(url, headers=dict(headers))
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return TransportResponse(status=response.status, body=response.read())
    except urllib.error.HTTPError as exc:
        return TransportResponse(status=exc.code, body=b"")
    except urllib.error.URLError as exc:
        if _is_timeout(exc):
            return TransportResponse(status=None, error="TIMEOUT")
        return TransportResponse(status=None, error=f"TRANSPORT:{exc}")
    except TimeoutError:
        return TransportResponse(status=None, error="TIMEOUT")
    except OSError as exc:
        if _is_timeout(exc):
            return TransportResponse(status=None, error="TIMEOUT")
        return TransportResponse(status=None, error=f"TRANSPORT:{exc}")


def scrub_credentials(environ: dict[str, str] | None = None) -> list[str]:
    """Process-locally remove the project's provider variables."""
    env = os.environ if environ is None else environ
    removed: list[str] = []
    for name in CREDENTIAL_VARIABLES:
        if name in env:
            env.pop(name, None)
            removed.append(name)
    return removed
