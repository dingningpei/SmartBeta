"""G6 — SEC EDGAR document fetcher bound to the hash-verified fetch plan.

This is the network-facing half of the gate-6 audit (spec section 10).  It
opens no socket by itself: every request goes through an injected transport,
so the whole module is exercised offline with a fake transport in tests.  It
never reads a provider credential and never writes the SEC contact to a log,
an artifact or Git.

The retriever is deliberately narrow:

* only ``GET https://www.sec.gov/Archives/<Filename>`` for a Filename present
  in the fetch plan is allowed;
* the SEC fair-access ``User-Agent`` contact comes from ``SEC_CONTACT`` and
  must be present, else nothing is fetched;
* at most 4 requests per second, at most 1 GET per planned filing plus
  retries, and a hard cap of 2,500 requests;
* retries only 429/5xx/timeouts (twice), never 403 or 404;
* atomic, SHA-256-addressed caching, so a resumed run skips verified files.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

REQUEST_CAP = 2500
RETRY_STATUS = frozenset({429, 500, 502, 503, 504})
BACKOFFS = (2.0, 8.0)
USER_AGENT_PREFIX = "SmartBeta-Gate6-Audit"
SEC_HOST = "www.sec.gov"
SEC_PATH_PREFIX = "/Archives/edgar/data/"

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
class PlanFiling:
    specimen_id: str
    cik: str
    accession: str
    filename: str


@dataclass(frozen=True)
class TransportResponse:
    status: int
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


def plan_sha256(filings: Sequence[PlanFiling]) -> str:
    payload = [
        {
            "specimen_id": f.specimen_id,
            "cik": f.cik,
            "accession": f.accession,
            "filename": f.filename,
        }
        for f in filings
    ]
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


_compute_plan_sha256 = plan_sha256


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build_url(filename: str) -> str:
    if not filename.startswith("/"):
        filename = "/" + filename
    return f"https://{SEC_HOST}{filename}"


class SecFetcher:
    """A fail-closed, cache-backed SEC document retriever."""

    def __init__(
        self,
        filings: Sequence[PlanFiling],
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
    ) -> None:
        self._filings = list(filings)
        actual = _compute_plan_sha256(self._filings)
        if actual != plan_sha256:
            raise FetchPlanMismatch(
                f"fetch-plan hash {actual} != bound {plan_sha256}"
            )
        self._bound_plan_sha256 = plan_sha256
        self._allowed = {f.filename for f in self._filings}
        self._transport = transport
        self._contact = contact if contact is not None else os.environ.get("SEC_CONTACT")
        if not self._contact:
            raise ContactMissing("SEC_CONTACT is required for SEC retrieval")
        self._cache_dir = Path(cache_dir) if cache_dir is not None else None
        self._cache_index = dict(cache_index or {})
        self._clock = clock
        self._sleep = sleep
        self._max_requests = max_requests
        self._min_interval = 1.0 / max_rate if max_rate > 0 else 0.0
        self._last_request: float | None = None
        self._requests = 0
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

    # -- allowlist ---------------------------------------------------------

    def _check_plan(self) -> None:
        actual = plan_sha256(self._filings)
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

    def _request(self, url: str, *, attempt: int) -> TransportResponse:
        if self._requests >= self._max_requests:
            return TransportResponse(status=None, error="REQUEST_CAP_REACHED")
        self._throttle()
        self._requests += 1
        try:
            return self._transport(url, {"User-Agent": self._user_agent()})
        except TimeoutError:
            return TransportResponse(status=None, error="TIMEOUT")
        except OSError as exc:  # network-level failure
            return TransportResponse(status=None, error=f"OSERROR:{exc}")

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

    def fetch_filing(self, filing: PlanFiling) -> FetchOutcome:
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
        if self._requests >= self._max_requests:
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
                self._record(url, response, attempt=attempt, outcome="REQUEST_CAP_REACHED")
                return FetchOutcome(
                    specimen_id=filing.specimen_id,
                    accession=filing.accession,
                    outcome="REQUEST_CAP_REACHED",
                )
            status = response.status
            if status == 200:
                digest, path = self._write_cache(filing.accession, response.body or b"")
                self._record(url, response, attempt=attempt, outcome="OK")
                self._retrieved[filing.accession] = digest
                return FetchOutcome(
                    specimen_id=filing.specimen_id,
                    accession=filing.accession,
                    outcome="FETCHED",
                    cached_path=path,
                    doc_sha256=digest,
                )
            # 403 and 404 are never retried: neither is in RETRY_STATUS nor a
            # timeout, so they fall straight through to UNRETRIEVABLE below.
            retryable = (
                status in RETRY_STATUS
                or response.error == "TIMEOUT"
                or (response.error or "").startswith("OSERROR")
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
        return [self.fetch_filing(filing) for filing in self._filings]

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


def scrub_credentials(environ: dict[str, str] | None = None) -> list[str]:
    """Process-locally remove the project's provider variables."""
    env = os.environ if environ is None else environ
    removed: list[str] = []
    for name in CREDENTIAL_VARIABLES:
        if name in env:
            env.pop(name, None)
            removed.append(name)
    return removed
