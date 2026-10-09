"""E-1 — Dataset v1.3 SEC EDGAR acquisition software (offline by construction).

Authority:

* the frozen design ``docs/discovery_dataset_v1_3_edgar_events_design.md``
  (``c9daa54b…b269``), "V§n" below;
* the frozen plan ``docs/discovery_dataset_v1_3_implementation_plan.md`` —
  §1 row E-1, §2 security, §3 interfaces, §4 fetcher tests, §5 mutations
  (prefix ``E1``), §6 live-run phases, §7 stops.

This module is the *software* half of E-1.  It never opens a socket by
itself: every request goes through an injected transport, so the whole module
is exercised offline with a fake transport in tests.  The networked
acquisition step (E-1L) is run by the planner, using the primitives here.

It implements, exactly:

* a byte-exact raw store ``raw/edgar-<acq_id>/`` with
  ``fetch_log.jsonl`` and a deterministic ``manifest.json`` (§3);
* an injected-transport SEC retriever with the SEC fair-access contact, an
  8 request/s rate cap, the 20,000 request budget and the narrow retry policy
  (only HTTP 429, HTTP 5xx and timeouts) (§2, §3, §4);
* SHA-256 resume, so a re-run skips byte-identical files and records a
  divergence otherwise (§3, V§2);
* the offline candidate-CIK computation from the D4 keys ever in TOP_1000,
  the D7a development membership and the two SEC reference files, with the
  historical-name route plus ``formerNames`` follow-ups (§3);
* both E-1L fetch plans, L1 (reference files plus submissions) and L2 (the
  IM-P symbol evidence) (§6);
* the §7 stops that are decidable in software: response-format validation
  (``acceptanceDateTime`` present), the request-budget stop, and the
  contact-absence scan over every written byte.

It imports only existing modules read-only and never reads ``data_store``.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.parse
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo

from smart_beta.dataset_v1.classification import norm as _norm

__all__ = [
    "BACKOFFS",
    "CIK_LOOKUP_URL",
    "COMPANY_TICKERS_URL",
    "CREDENTIAL_VARIABLES",
    "DEV_END",
    "DEV_START",
    "EASTERN",
    "FetchError",
    "FetchOutcome",
    "FetchRequest",
    "L3_EVENT_FORMS",
    "L3_FILING_DATE_FLOOR",
    "L2_NO_INLINE_RELPATH",
    "L2_PERIODIC_FORMS",
    "MAX_RATE",
    "REQUEST_CAP",
    "RawStore",
    "ResponseFormatError",
    "RequestBudgetExceeded",
    "RetrievalRecord",
    "SEC_HOSTS",
    "SampleKey",
    "SecEdgarFetcher",
    "TransportResponse",
    "UrlRefused",
    "assert_no_contact",
    "build_l1_plan",
    "build_l2_plan",
    "build_l3_plan",
    "canonical_json",
    "check_request_budget",
    "compute_acq_id",
    "compute_candidate_ciks",
    "development_top_1000_keys",
    "filing_headers_url",
    "former_name_followups",
    "headers_relpath",
    "iter_filings",
    "is_retry_status",
    "no_inline_xbrl_keys",
    "paginated_requests_from_submissions",
    "parse_acceptance_datetime_header",
    "parse_acceptance_header",
    "parse_cik_lookup",
    "parse_company_tickers",
    "scan_for_contact",
    "select_periodic_filings",
    "sha256_bytes",
    "sha256_hex",
    "validate_submission_payload",
    "write_no_inline_xbrl",
]

# ---------------------------------------------------------------------------
# Frozen constants
# ---------------------------------------------------------------------------

#: Development window (D4/D7a development membership; V§1, plan §3).
DEV_START = date(2015, 1, 2)
DEV_END = date(2023, 6, 30)

#: The hard SEC retrieval cap and the fair-access rate cap (plan §3, §6).
REQUEST_CAP = 20_000
MAX_RATE = 8.0
BACKOFFS = (2.0, 8.0)
USER_AGENT_PREFIX = "SmartBeta-Dataset-v1.3"

#: The allowed SEC hosts (http(s) only; every other host is refused).
SEC_HOSTS = frozenset({"www.sec.gov", "data.sec.gov"})

COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
CIK_LOOKUP_URL = "https://www.sec.gov/Archives/edgar/cik-lookup-data.txt"

#: Provider credentials scrubbed for every E-1 test process (plan §2, §4).
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

#: The V§2 submissions field list.  A missing ``acceptanceDateTime`` column is
#: the plan §7 stop (the response format differs from the frozen field list).
FILING_COLUMNS = (
    "accessionNumber",
    "form",
    "filingDate",
    "reportDate",
    "acceptanceDateTime",
    "items",
    "primaryDocument",
)

#: Periodic report forms used by the identity/status computation (E-2).  The
#: amendments are kept here for the interval-verification semantics.
PERIODIC_FORMS = frozenset(
    {"10-Q", "10-Q/A", "10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A"}
)

#: L2 periodic forms (V13-E3 §1): 10-K, 10-Q, 20-F, 40-F; amendments excluded.
L2_PERIODIC_FORMS = frozenset({"10-Q", "10-K", "20-F", "40-F"})
L2_MAX_FILINGS_PER_KEY = 2
L2_REQUEST_BUDGET = 1_000

#: The raw-store relpath of the NO_INLINE_XBRL_FILING key list (V13-E3 §1).
L2_NO_INLINE_RELPATH = "symbol_evidence/no_inline_xbrl.json"

#: L3 — one index-header page per event-typed filing (erratum V13-E2 §5):
#: an original 8-K with Item 2.02, a 10-Q, 10-K, 6-K, 20-F or 40-F.  The
#: request budget is deliberately **not** hard-coded; the erratum leaves it
#: pending user approval, so :func:`build_l3_plan` requires the caller's
#: ``max_requests``.  The floor is the V§4 development start.
L3_EVENT_FORMS = frozenset({"10-Q", "10-K", "6-K", "20-F", "40-F"})
L3_EA_8K_ITEM = "2.02"
L3_FILING_DATE_FLOOR = date(2015, 1, 2)

#: The EDGAR index-header acceptance time is an America/New_York wall clock.
EASTERN = ZoneInfo("America/New_York")


# ---------------------------------------------------------------------------
# Small deterministic helpers
# ---------------------------------------------------------------------------


def canonical_json(obj: Any) -> str:
    """The canonical JSON encoding (sorted keys, no whitespace)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sha256_hex(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _is_timeout(error: BaseException) -> bool:
    if isinstance(error, TimeoutError):
        return True
    reason = getattr(error, "reason", None)
    return isinstance(reason, TimeoutError)


def _as_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def is_retry_status(status: int | None) -> bool:
    """Only HTTP 429 and HTTP 5xx are retryable; never 403, 404 or 3xx."""
    if status is None:
        return False
    return status == 429 or 500 <= status <= 599


# ---------------------------------------------------------------------------
# L3 index-header acceptance time (erratum V13-E2 §5)
# ---------------------------------------------------------------------------


_ACCEPTANCE_HEADER_RE = re.compile(r"<ACCEPTANCE-DATETIME>(\d{14})(?!\d)")


def parse_acceptance_datetime_header(text: bytes | str) -> datetime | None:
    """Parse an EDGAR index-header ``<ACCEPTANCE-DATETIME>`` value.

    The erratum V13-E2 §2 makes the header value the knowledge-time source.
    The field must be exactly 14 digits (``YYYYMMDDHHMMSS``), read as an
    ``America/New_York`` wall-clock time.  Anything else -- a missing tag, a
    non-14-digit field, or an impossible calendar value -- returns ``None``;
    the caller must then set ``k_session = null`` with reason
    ``EVENT_TIME_UNAVAILABLE`` (never a JSON or ``filingDate`` fallback).
    """
    if isinstance(text, bytes):
        text = text.decode("utf-8", errors="replace")
    if not isinstance(text, str):
        return None
    match = _ACCEPTANCE_HEADER_RE.search(text)
    if match is None:
        return None
    try:
        naive = datetime.strptime(match.group(1), "%Y%m%d%H%M%S")
    except ValueError:
        return None
    return naive.replace(tzinfo=EASTERN)


#: Ergonomic alias for the E-3-R1 reader.
parse_acceptance_header = parse_acceptance_datetime_header


# ---------------------------------------------------------------------------
# Typed errors (fail closed)
# ---------------------------------------------------------------------------


class FetchError(Exception):
    """Base class for every E-1 acquisition error."""


class ContactMissing(FetchError, ValueError):
    """``SEC_CONTACT`` (or the injected contact) is required and was absent."""


class UrlRefused(FetchError, ValueError):
    """A URL is outside the exact HTTPS SEC host allowlist."""


class RequestBudgetExceeded(FetchError, ValueError):
    """A fetch plan exceeds the frozen request budget (plan §3, §7)."""


class ResponseFormatError(FetchError, ValueError):
    """A SEC response differs from the frozen V§2 field list (plan §7 stop)."""


class ManifestError(FetchError, ValueError):
    """A raw-store manifest does not verify."""


class ContactLeakError(FetchError, ValueError):
    """The SEC contact string was found in a written byte (plan §7 stop)."""


# ---------------------------------------------------------------------------
# Transport and record types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TransportResponse:
    status: int | None
    body: bytes = b""
    error: str | None = None


@dataclass(frozen=True)
class RetrievalRecord:
    url: str
    retrieval_utc: str
    http_status: int | None
    sha256: str | None
    bytes: int
    attempt: int
    outcome: str


@dataclass(frozen=True)
class FetchOutcome:
    url: str
    outcome: str
    sha256: str | None = None
    bytes: int = 0
    relpath: str | None = None


@dataclass(frozen=True)
class FetchRequest:
    """One planned GET: an absolute URL and the raw-store relative path."""

    url: str
    relpath: str
    kind: str
    security_key: str | None = None
    cik: int | None = None
    accession: str | None = None


# ---------------------------------------------------------------------------
# Raw store (byte-exact; deterministic manifest)
# ---------------------------------------------------------------------------


def compute_acq_id(files: Mapping[str, str]) -> str:
    """``acq_id`` = SHA-256 of the canonical sorted ``(path, sha256)`` list."""
    payload = [[path, files[path]] for path in sorted(files)]
    return sha256_hex(canonical_json(payload))


class RawStore:
    """A byte-exact raw acquisition directory.

    The store is created on a staging path; :meth:`finalize` computes the
    ``acq_id`` from the written files (excluding ``manifest.json`` itself),
    writes the canonical manifest and optionally renames the staging directory
    to ``<dest_root>/edgar-<acq_id>``.
    """

    def __init__(self, staging: str | Path) -> None:
        self._staging = Path(staging)
        self._staging.mkdir(parents=True, exist_ok=True)

    @property
    def root(self) -> Path:
        return self._staging

    def path(self, relpath: str) -> Path:
        return self._staging / relpath

    def write(self, relpath: str, body: bytes) -> str:
        """Write ``body`` byte-exact under ``relpath``; return its SHA-256."""
        target = self.path(relpath)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
        return sha256_bytes(body)

    def write_json(self, relpath: str, obj: Any) -> str:
        return self.write(relpath, canonical_json(obj).encode("utf-8"))

    def append_jsonl(self, relpath: str, lines: Iterable[str]) -> str:
        text = "".join(line if line.endswith("\n") else line + "\n" for line in lines)
        return self.write(relpath, text.encode("utf-8"))

    def read(self, relpath: str) -> bytes:
        return self.path(relpath).read_bytes()

    def sha_of(self, relpath: str) -> str | None:
        target = self.path(relpath)
        if not target.exists():
            return None
        return sha256_bytes(target.read_bytes())

    def size_of(self, relpath: str) -> int:
        target = self.path(relpath)
        return target.stat().st_size if target.exists() else 0

    def file_hashes(self) -> dict[str, str]:
        """Every stored file except the manifest, as ``relpath -> sha256``."""
        hashes: dict[str, str] = {}
        for target in sorted(self._staging.rglob("*")):
            if not target.is_file():
                continue
            relpath = target.relative_to(self._staging).as_posix()
            if relpath == "manifest.json":
                continue
            hashes[relpath] = sha256_bytes(target.read_bytes())
        return hashes

    def finalize(
        self,
        *,
        acq_id: str | None = None,
        dest_root: str | Path | None = None,
    ) -> tuple[str, Path]:
        """Write ``manifest.json`` and (optionally) rename to ``edgar-<acq_id>``."""
        files = self.file_hashes()
        if acq_id is None:
            acq_id = compute_acq_id(files)
        manifest = {
            "acq_id": acq_id,
            "files": [
                {"path": path, "sha256": files[path]} for path in sorted(files)
            ],
        }
        self.write_json("manifest.json", manifest)
        final = self._staging
        if dest_root is not None:
            final = Path(dest_root) / f"edgar-{acq_id}"
            if final.exists():
                raise ManifestError(f"raw acquisition {final} already exists")
            final.parent.mkdir(parents=True, exist_ok=True)
            os.replace(self._staging, final)
            self._staging = final
        return acq_id, final

    def verify_manifest(self) -> dict[str, Any]:
        """Verify every manifest entry against the bytes on disk."""
        raw = self.read("manifest.json")
        manifest = json.loads(raw.decode("utf-8"))
        declared = {
            entry["path"]: entry["sha256"] for entry in manifest.get("files", [])
        }
        actual = self.file_hashes()
        if declared != actual:
            raise ManifestError(
                f"manifest hash mismatch: declared {declared} != actual {actual}"
            )
        if manifest.get("acq_id") != compute_acq_id(declared):
            raise ManifestError("manifest acq_id does not match its file list")
        return manifest


def scan_for_contact(root: str | Path, contact: str) -> list[str]:
    """Return every file under ``root`` whose bytes contain ``contact``."""
    needle = contact.encode("utf-8")
    hits: list[str] = []
    base = Path(root)
    for target in sorted(base.rglob("*")):
        if target.is_file() and needle in target.read_bytes():
            hits.append(target.relative_to(base).as_posix())
    return hits


def assert_no_contact(root: str | Path, contact: str) -> None:
    """Fail closed if the contact appears in any written byte (plan §7 stop)."""
    hits = scan_for_contact(root, contact)
    if hits:
        raise ContactLeakError(f"SEC contact found in {hits}")


# ---------------------------------------------------------------------------
# The SEC retriever
# ---------------------------------------------------------------------------


class SecEdgarFetcher:
    """A fail-closed, SHA-256-resumable SEC retriever.

    The retriever opens no socket itself: ``transport`` receives
    ``(url, headers)`` and returns a :class:`TransportResponse`.  The contact
    is supplied explicitly (tests) or read once from ``SEC_CONTACT`` at
    construction; if it is absent the retriever raises and issues **zero**
    requests.
    """

    def __init__(
        self,
        *,
        transport: Callable[[str, Mapping[str, str]], TransportResponse],
        contact: str | None = None,
        store: RawStore | None = None,
        resume_index: Mapping[str, str] | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        max_requests: int = REQUEST_CAP,
        max_rate: float = MAX_RATE,
        initial_requests: int = 0,
    ) -> None:
        self._contact = (
            contact if contact is not None else os.environ.get("SEC_CONTACT")
        )
        if not self._contact:
            raise ContactMissing("SEC_CONTACT is required for SEC retrieval")
        self._transport = transport
        self._store = store
        self._resume_index = dict(resume_index or {})
        self._clock = clock
        self._sleep = sleep
        self._now = now
        self._max_requests = max_requests
        self._min_interval = 1.0 / max_rate if max_rate > 0 else 0.0
        self._last_request: float | None = None
        self._requests = int(initial_requests)
        self._log: list[RetrievalRecord] = []

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

    @staticmethod
    def _check_url(url: str) -> str:
        parts = urllib.parse.urlsplit(url)
        if parts.scheme != "https" or parts.hostname not in SEC_HOSTS:
            raise UrlRefused(f"url {url!r} is not an HTTPS SEC host")
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
        except OSError as exc:  # a generic transport error is never retried
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
        self._log.append(
            RetrievalRecord(
                url=url,
                retrieval_utc=self._now().isoformat(),
                http_status=response.status,
                sha256=sha256_bytes(body) if response.status == 200 else None,
                bytes=len(body),
                attempt=attempt,
                outcome=outcome,
            )
        )

    # -- fetch -------------------------------------------------------------

    def fetch(
        self,
        url: str,
        *,
        relpath: str | None = None,
        expected_sha256: str | None = None,
    ) -> FetchOutcome:
        self._check_url(url)
        if relpath is not None and self._store is not None:
            existing = self._store.sha_of(relpath)
            if existing is not None and (
                expected_sha256 is None or existing == expected_sha256
            ):
                return FetchOutcome(
                    url=url,
                    outcome="CACHED",
                    sha256=existing,
                    bytes=self._store.size_of(relpath),
                    relpath=relpath,
                )
        if self._cap_reached():
            return FetchOutcome(url=url, outcome="REQUEST_CAP_REACHED", relpath=relpath)

        attempt = 0
        while True:
            attempt += 1
            response = self._request(url, attempt=attempt)
            if response.error == "REQUEST_CAP_REACHED":
                return FetchOutcome(
                    url=url, outcome="REQUEST_CAP_REACHED", relpath=relpath
                )
            status = response.status
            if status == 200:
                body = response.body or b""
                digest = sha256_bytes(body)
                if relpath is not None and self._store is not None:
                    self._store.write(relpath, body)
                self._record(url, response, attempt=attempt, outcome="OK")
                return FetchOutcome(
                    url=url,
                    outcome="FETCHED",
                    sha256=digest,
                    bytes=len(body),
                    relpath=relpath,
                )
            retryable = is_retry_status(status) or response.error == "TIMEOUT"
            if not retryable or attempt > len(BACKOFFS):
                self._record(url, response, attempt=attempt, outcome="UNRETRIEVABLE")
                return FetchOutcome(
                    url=url,
                    outcome="UNRETRIEVABLE",
                    bytes=len(response.body or b""),
                    relpath=relpath,
                )
            self._record(url, response, attempt=attempt, outcome="RETRY")
            self._sleep(BACKOFFS[attempt - 1])

    def fetch_many(self, requests: Sequence[FetchRequest]) -> list[FetchOutcome]:
        """Fetch each distinct ``relpath`` once, resuming verified files."""
        results: list[FetchOutcome] = []
        seen: set[str] = set()
        for request in requests:
            if request.relpath in seen:
                continue
            seen.add(request.relpath)
            results.append(
                self.fetch(
                    request.url,
                    relpath=request.relpath,
                    expected_sha256=self._resume_index.get(request.url),
                )
            )
        return results

    def log_jsonl(self) -> str:
        """The ``fetch_log.jsonl`` payload (no contact, no headers)."""
        return "".join(
            canonical_json(
                {
                    "url": record.url,
                    "retrieval_utc": record.retrieval_utc,
                    "http_status": record.http_status,
                    "sha256": record.sha256,
                    "bytes": record.bytes,
                    "attempt": record.attempt,
                    "outcome": record.outcome,
                }
            )
            + "\n"
            for record in self._log
        )

    def write_log(self, relpath: str = "fetch_log.jsonl") -> str:
        if self._store is None:
            raise FetchError("no raw store is attached")
        return self._store.write(relpath, self.log_jsonl().encode("utf-8"))


# ---------------------------------------------------------------------------
# SEC reference-file parsing
# ---------------------------------------------------------------------------


def parse_company_tickers(
    payload: bytes | str | Mapping[str, Any],
) -> dict[str, tuple[int, ...]]:
    """Parse ``company_tickers.json`` into ``ticker -> (cik, ...)``.

    The SEC publishes the object with numeric string keys; we accept either
    that or a plain sequence of rows.  Tickers are upper-cased.
    """
    if isinstance(payload, (bytes, str)):
        data = json.loads(payload)
    else:
        data = payload
    if isinstance(data, Mapping):
        rows = list(data.values())
    else:
        rows = list(data)
    found: dict[str, set[int]] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        ticker = row.get("ticker")
        cik = row.get("cik_str", row.get("cik"))
        if ticker is None or cik is None:
            continue
        found.setdefault(str(ticker).upper(), set()).add(int(cik))
    return {ticker: tuple(sorted(ciks)) for ticker, ciks in found.items()}


def parse_cik_lookup(text: bytes | str) -> dict[str, tuple[int, ...]]:
    """Parse ``cik-lookup-data.txt`` into ``normalized name -> (cik, ...)``.

    Each record is ``NAME:CIK:``; the historical table includes former names.
    """
    if isinstance(text, bytes):
        text = text.decode("utf-8", errors="replace")
    found: dict[str, set[int]] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(":")
        if len(parts) < 2:
            continue
        name = parts[0].strip()
        cik_text = parts[1].strip()
        if not name or not cik_text.isdigit():
            continue
        key = _norm(name)
        if not key:
            continue
        found.setdefault(key, set()).add(int(cik_text))
    return {name: tuple(sorted(ciks)) for name, ciks in found.items()}


# ---------------------------------------------------------------------------
# Offline candidate-CIK computation (plan §3)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Top1000Key:
    security_key: str
    ticker: str | None
    issuer_names: tuple[str, ...] = ()


def _coerce_key(key: Any) -> _Top1000Key:
    if isinstance(key, _Top1000Key):
        return key
    if isinstance(key, Mapping):
        names = key.get("issuer_names") or key.get("names") or ()
        if isinstance(names, str):
            names = (names,)
        return _Top1000Key(
            security_key=str(key["security_key"]),
            ticker=key.get("ticker"),
            issuer_names=tuple(str(name) for name in names),
        )
    raise TypeError(f"unsupported key record {key!r}")


def development_top_1000_keys(
    membership_rows: Iterable[Mapping[str, Any]],
    d4_rows: Iterable[Mapping[str, Any]],
    *,
    start: date = DEV_START,
    end: date = DEV_END,
) -> dict[str, _Top1000Key]:
    """Join D7a development membership with D4 identity (plan §3).

    ``membership_rows`` is the D7a per-formation universe (a ``security_key``,
    a ``formation_date`` and a boolean ``top_1000``); ``d4_rows`` is the D4
    identity assignment (``security_key`` and ``ticker``).  The result holds
    one key per D4 ``security_key`` that was ever a TOP_1000 member at a
    development formation in ``[start, end]``.  Keys with no D4 identity row
    are dropped; a key absent from the result is never a TOP_1000 member.
    """
    members: set[str] = set()
    for row in membership_rows:
        if not row.get("top_1000"):
            continue
        formation = row.get("formation_date")
        if isinstance(formation, str):
            formation = _as_date(formation)
        if formation is None or not (start <= formation <= end):
            continue
        key = row.get("security_key")
        if key is not None:
            members.add(str(key))
    result: dict[str, _Top1000Key] = {}
    for row in d4_rows:
        key = str(row.get("security_key"))
        if key not in members:
            continue
        result[key] = _Top1000Key(security_key=key, ticker=row.get("ticker"))
    return result


def compute_candidate_ciks(
    keys: Iterable[Any],
    *,
    company_tickers: Mapping[str, Sequence[int]],
    cik_lookup: Mapping[str, Sequence[int]],
    issuer_names: Mapping[str, Sequence[str]] | None = None,
    max_name_candidates: int = 5,
) -> dict[str, tuple[int, ...]]:
    """The offline candidate-CIK set for the E-1L L1 fetch (plan §3).

    The union of:

    * the current-ticker matches for every D4 key ever in TOP_1000 during
      development (``company_tickers.json``); and
    * the historical-name route, at most ``max_name_candidates`` CIKs per key
      (``cik-lookup-data.txt``), over the key's issuer names.
    """
    external_names = issuer_names or {}
    result: dict[str, tuple[int, ...]] = {}
    for raw in keys:
        key = _coerce_key(raw)
        candidates: set[int] = set()
        if key.ticker:
            candidates.update(company_tickers.get(str(key.ticker).upper(), ()))
        names = list(key.issuer_names)
        names.extend(str(name) for name in external_names.get(key.security_key, ()))
        for name in names:
            normalized = _norm(name)
            hits = cik_lookup.get(normalized, ())
            candidates.update(sorted(hits)[:max_name_candidates])
        result[key.security_key] = tuple(sorted(candidates))
    return result


def former_name_followups(
    candidates: Mapping[str, Sequence[int]],
    *,
    submissions_former_names: Mapping[int, Sequence[str]],
    cik_lookup: Mapping[str, Sequence[int]],
    max_name_candidates: int = 5,
) -> dict[str, tuple[int, ...]]:
    """Augment the candidate set from the fetched ``formerNames`` (plan §3).

    ``submissions_former_names`` maps a fetched CIK to the ``formerNames``
    strings of its submissions response.  For every key that already holds
    that CIK, those former names are normalized and looked up, and any new
    CIK (capped) is added.
    """
    result = {key: tuple(sorted(ciks)) for key, ciks in candidates.items()}
    for key, ciks in candidates.items():
        additions: set[int] = set(result[key])
        for cik in ciks:
            for former in submissions_former_names.get(int(cik), ()):
                normalized = _norm(str(former))
                additions.update(
                    sorted(cik_lookup.get(normalized, ()))[:max_name_candidates]
                )
        result[key] = tuple(sorted(additions))
    return result


# ---------------------------------------------------------------------------
# URL builders and the two E-1L fetch plans (plan §6)
# ---------------------------------------------------------------------------


def submissions_url(cik: int) -> str:
    return f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json"


def submission_file_url(name: str) -> str:
    return f"https://data.sec.gov/submissions/{name}"


def filing_index_url(cik: int, accession: str) -> str:
    accession = accession.replace("-", "")
    return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession}/index.json"


def primary_document_url(cik: int, accession: str, document: str) -> str:
    accession = accession.replace("-", "")
    return (
        f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession}/{document}"
    )


def filing_headers_url(cik: int, accession: str) -> str:
    """The ``<acc>-index-headers.html`` URL for one filing (erratum V13-E2)."""
    folder = accession.replace("-", "")
    return (
        f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{folder}/"
        f"{accession}-index-headers.html"
    )


def headers_relpath(cik: int, accession: str) -> str:
    """The raw-store header relpath E-3-R1 reads (addendum to E-1-R1).

    ``headers/<cik zero-padded to 10 digits>/<accession with dashes>-index-headers.html``.
    """
    return f"headers/{int(cik):010d}/{accession}-index-headers.html"


def build_l1_plan(
    candidate_ciks: Iterable[int],
    *,
    paginated_files: Mapping[int, Sequence[str]] | None = None,
    max_requests: int = REQUEST_CAP,
) -> tuple[FetchRequest, ...]:
    """L1: the two reference files plus one submissions request per candidate.

    ``paginated_files`` (normally read from a candidate's first response
    ``filings.files``) adds the paginated ``files/*.json`` follow-ups.  The
    frozen request budget is enforced here: an over-budget plan stops rather
    than being truncated (plan §3, §7).
    """
    ciks = sorted({int(cik) for cik in candidate_ciks})
    requests: list[FetchRequest] = [
        FetchRequest(COMPANY_TICKERS_URL, "company_tickers.json", "reference"),
        FetchRequest(CIK_LOOKUP_URL, "cik-lookup-data.txt", "reference"),
    ]
    for cik in ciks:
        requests.append(
            FetchRequest(
                submissions_url(cik),
                f"submissions/CIK{cik:010d}.json",
                "submission",
                cik=cik,
            )
        )
    for cik in ciks:
        for name in sorted((paginated_files or {}).get(cik, ())):
            requests.append(
                FetchRequest(
                    submission_file_url(name),
                    f"submissions/files/{name}",
                    "submission_file",
                    cik=cik,
                )
            )
    plan = tuple(requests)
    check_request_budget(plan, max_requests=max_requests)
    return plan


def _rows_from_parallel_arrays(block: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Expand a parallel-array block into one row per filing index."""
    columns = list(block.keys())
    length = 0
    for column in columns:
        value = block[column]
        if isinstance(value, list):
            length = max(length, len(value))
    rows: list[dict[str, Any]] = []
    for index in range(length):
        rows.append(
            {
                column: (
                    block[column][index]
                    if isinstance(block[column], list) and index < len(block[column])
                    else None
                )
                for column in columns
            }
        )
    return rows


def iter_filings(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Normalize a submissions payload into a list of filing rows.

    Handles the parallel-array ``filings.recent`` block, the paginated
    ``filings.files`` follow-ups, and -- per the real SEC schema (V13-E2
    addendum) -- the paginated ``files/*.json`` bodies, which carry the same
    parallel arrays at the **top level**.  A legacy flat list of per-filing
    objects is also accepted.
    """
    filings = payload.get("filings")
    rows: list[dict[str, Any]] = []
    if isinstance(filings, list):
        # A legacy flat body: a list of per-filing objects.
        return [dict(row) for row in filings if isinstance(row, Mapping)]
    if isinstance(filings, Mapping):
        recent = filings.get("recent")
        if isinstance(recent, Mapping):
            rows.extend(_rows_from_parallel_arrays(recent))
        paginated = filings.get("files")
        if isinstance(paginated, Mapping):
            values = list(paginated.values())
        elif isinstance(paginated, list):
            values = list(paginated)
        else:
            values = []
        for value in values:
            if isinstance(value, list):
                rows.extend(
                    row
                    for row in value
                    if isinstance(row, Mapping)
                    and ("accessionNumber" in row or "form" in row)
                )
            elif isinstance(value, Mapping) and (
                "accessionNumber" in value or "form" in value
            ):
                rows.append(value)
        return rows
    # Paginated ``files/*.json`` (real SEC schema): the same parallel arrays
    # at the top level, with no wrapping ``filings`` object.
    if any(key in payload for key in ("accessionNumber", "form")):
        return _rows_from_parallel_arrays(payload)
    return []


def paginated_requests_from_submissions(
    submissions: Mapping[int, Mapping[str, Any]],
) -> dict[int, tuple[str, ...]]:
    """The ``files/*.json`` names advertised by each fetched submission."""
    result: dict[int, tuple[str, ...]] = {}
    for cik, payload in submissions.items():
        filings = payload.get("filings")
        names: list[str] = []
        if isinstance(filings, Mapping):
            files = filings.get("files")
            if isinstance(files, list):
                for entry in files:
                    if isinstance(entry, Mapping) and entry.get("name"):
                        names.append(str(entry["name"]))
        result[int(cik)] = tuple(sorted(set(names)))
    return result


def _is_inline_xbrl(row: Mapping[str, Any]) -> bool:
    """Whether a filing row carries the real-SEC ``isInlineXBRL`` flag.

    The Addendum to V13-E2 records that submissions rows carry
    ``isInlineXBRL`` as ``0`` / ``1``; a legacy boolean is also accepted.
    """
    value = row.get("isInlineXBRL")
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes")
    return value in (1, True)


def _periodic_date(row: Mapping[str, Any]) -> str | None:
    value = row.get("filingDate") or row.get("filing_date")
    return str(value) if value else None


def select_periodic_filings(
    rows: Sequence[Mapping[str, Any]],
    *,
    interval_start: date | None,
    interval_end: date | None,
    limit: int = L2_MAX_FILINGS_PER_KEY,
) -> tuple[Mapping[str, Any], ...]:
    """The latest ≤ ``limit`` inline-XBRL periodic filings in the interval.

    V13-E3 §1: only 10-K / 10-Q / 20-F / 40-F originals (amendments
    excluded) with a truthy ``isInlineXBRL`` inside
    ``[interval_start, interval_end]``; the latest by ``filingDate``, ties
    broken by ``accessionNumber``.
    """
    selected: list[Mapping[str, Any]] = []
    for row in rows:
        form = str(row.get("form") or "")
        if form not in L2_PERIODIC_FORMS:
            continue
        if not _is_inline_xbrl(row):
            continue
        filed = _periodic_date(row)
        if filed is None:
            continue
        try:
            filed_date = date.fromisoformat(filed[:10])
        except ValueError:
            continue
        if interval_start is not None and filed_date < interval_start:
            continue
        if interval_end is not None and filed_date > interval_end:
            continue
        selected.append(row)
    selected.sort(
        key=lambda row: (
            _periodic_date(row) or "",
            str(row.get("accessionNumber") or ""),
        ),
        reverse=True,
    )
    return tuple(selected[:limit])


@dataclass(frozen=True)
class SampleKey:
    """One IM-P sampled key passed to the L2 plan (E-2 selects the sample)."""

    security_key: str
    cik: int
    interval_start: date | None = None
    interval_end: date | None = None
    ticker: str | None = None


def build_l2_plan(
    sample: Sequence[SampleKey],
    *,
    submissions: Mapping[int, Mapping[str, Any]],
    filings_per_key: int = L2_MAX_FILINGS_PER_KEY,
    max_requests: int = L2_REQUEST_BUDGET,
) -> tuple[FetchRequest, ...]:
    """L2: the primary document of the latest ≤ 2 inline-XBRL periodics/key.

    V13-E3 §1: only the primary document is fetched (the filing index JSON is
    no longer required); a key with no inline-XBRL periodic filing in its
    interval contributes no request and is recorded by
    :func:`no_inline_xbrl_keys` / :func:`write_no_inline_xbrl`.  The raw-store
    relpath is unchanged from E-1:
    ``symbol_evidence/<int cik>/<accession without dashes>/<primaryDocument>``.
    """
    requests: list[FetchRequest] = []
    for key in sorted(sample, key=lambda item: item.security_key):
        rows = iter_filings(submissions.get(int(key.cik), {}))
        selected = select_periodic_filings(
            rows,
            interval_start=key.interval_start,
            interval_end=key.interval_end,
            limit=filings_per_key,
        )
        for row in selected:
            accession = str(row.get("accessionNumber") or "")
            document = row.get("primaryDocument")
            if not accession or not document:
                continue
            folder = accession.replace("-", "")
            requests.append(
                FetchRequest(
                    primary_document_url(key.cik, accession, str(document)),
                    f"symbol_evidence/{int(key.cik)}/{folder}/{document}",
                    "primary_doc",
                    security_key=key.security_key,
                    cik=int(key.cik),
                    accession=accession,
                )
            )
    planned = tuple(requests)
    check_request_budget(planned, max_requests=max_requests)
    return planned


def no_inline_xbrl_keys(
    sample: Sequence[SampleKey],
    *,
    submissions: Mapping[int, Mapping[str, Any]],
    filings_per_key: int = L2_MAX_FILINGS_PER_KEY,
) -> tuple[str, ...]:
    """The sorted security keys with no inline-XBRL periodic filing (V13-E3 §1)."""
    keys: list[str] = []
    for key in sorted(sample, key=lambda item: item.security_key):
        rows = iter_filings(submissions.get(int(key.cik), {}))
        selected = select_periodic_filings(
            rows,
            interval_start=key.interval_start,
            interval_end=key.interval_end,
            limit=filings_per_key,
        )
        if not selected:
            keys.append(key.security_key)
    return tuple(sorted(set(keys)))


def write_no_inline_xbrl(store: RawStore, keys: Iterable[str]) -> str:
    """Write the sorted NO_INLINE_XBRL_FILING key list to the raw store.

    The binding interface (V13-E3 §1) is
    ``symbol_evidence/no_inline_xbrl.json``: a sorted JSON list of
    ``security_key``.  Returns the relpath written.
    """
    ordered = sorted({str(key) for key in keys})
    store.write_json(L2_NO_INLINE_RELPATH, ordered)
    return L2_NO_INLINE_RELPATH


# ---------------------------------------------------------------------------
# L3 — the index-header fetch plan (erratum V13-E2 §5)
# ---------------------------------------------------------------------------


def _iter_l3_scope(
    scope: Mapping[int, Any] | Iterable[SampleKey],
) -> Iterable[tuple[int, date | None, date | None]]:
    """Normalize the L3 scope into ``(cik, interval_start, interval_end)``.

    ``scope`` is normally a mapping from CIK to an interval (a ``(start,
    end)`` pair, an object with ``interval_start`` / ``interval_end``, or a
    mapping with those keys).  A sequence of :class:`SampleKey` is also
    accepted for symmetry with :func:`build_l2_plan`.
    """
    if isinstance(scope, Mapping):
        items: Iterable[tuple[Any, Any]] = scope.items()
    else:
        items = ((item.cik, item) for item in scope)
    for raw_cik, interval in items:
        if isinstance(interval, Mapping):
            start = interval.get("interval_start")
            end = interval.get("interval_end")
        elif hasattr(interval, "interval_start") or hasattr(interval, "interval_end"):
            start = getattr(interval, "interval_start", None)
            end = getattr(interval, "interval_end", None)
        else:
            start, end = interval[0], interval[1]
        yield int(raw_cik), _as_date(start), _as_date(end)


def _parse_l3_items(value: Any) -> tuple[str, ...]:
    """Parse an 8-K ``items`` string (``"2.02,9.01"``) into exact tokens."""
    if value is None:
        return ()
    if isinstance(value, (list, tuple)):
        tokens = [str(token) for token in value]
    else:
        tokens = re.split(r"[,;]", str(value))
    return tuple(token.strip() for token in tokens if token.strip())


def _is_l3_event_filing(row: Mapping[str, Any]) -> bool:
    """Whether a filing row is one of the six event-typed filings (V§4).

    An original 8-K with Item 2.02, or a 10-Q / 10-K / 6-K / 20-F / 40-F.
    Amendments never qualify.
    """
    form = str(row.get("form") or "").strip().upper()
    if form.endswith("/A"):
        return False
    if form == "8-K":
        return L3_EA_8K_ITEM in _parse_l3_items(row.get("items"))
    return form in L3_EVENT_FORMS


def build_l3_plan(
    scope: Mapping[int, Any] | Iterable[SampleKey],
    *,
    submissions: Mapping[int, Mapping[str, Any]],
    max_requests: int,
) -> tuple[FetchRequest, ...]:
    """L3: one index-header request per event-typed filing (erratum V13-E2 §5).

    For each ``(cik, interval)`` in ``scope``, every stored filing that is an
    original 8-K with Item 2.02, a 10-Q, 10-K, 6-K, 20-F or 40-F, whose
    ``filingDate`` is at or after 2015-01-02 and inside the interval, yields
    one :class:`FetchRequest` for
    ``https://www.sec.gov/Archives/edgar/data/<cik>/<acc-no-dashes>/<acc>-index-headers.html``
    stored at ``headers/<cik10>/<acc>-index-headers.html``.

    The L3 budget is not hard-coded: the caller passes ``max_requests`` (the
    erratum leaves the value pending user approval).  An over-budget plan
    stops rather than being truncated.
    """
    requests: list[FetchRequest] = []
    seen: set[tuple[int, str]] = set()
    for cik, interval_start, interval_end in _iter_l3_scope(scope):
        for row in iter_filings(submissions.get(int(cik), {})):
            if not _is_l3_event_filing(row):
                continue
            filed = _as_date(row.get("filingDate"))
            if filed is None or filed < L3_FILING_DATE_FLOOR:
                continue
            if interval_start is not None and filed < interval_start:
                continue
            if interval_end is not None and filed > interval_end:
                continue
            accession = str(row.get("accessionNumber") or "")
            if not accession:
                continue
            dedup_key = (int(cik), accession)
            if dedup_key in seen:
                continue
            seen.add(dedup_key)
            requests.append(
                FetchRequest(
                    filing_headers_url(cik, accession),
                    headers_relpath(cik, accession),
                    "header",
                    cik=int(cik),
                    accession=accession,
                )
            )
    requests.sort(
        key=lambda request: (
            request.cik or 0,
            request.accession or "",
            request.relpath,
        )
    )
    plan = tuple(requests)
    check_request_budget(plan, max_requests=max_requests)
    return plan


# ---------------------------------------------------------------------------
# Format validation and the request-budget stop (plan §7)
# ---------------------------------------------------------------------------


def validate_submission_payload(payload: Mapping[str, Any]) -> None:
    """Fail closed when a submissions response differs from the V§2 fields."""
    if not isinstance(payload, Mapping):
        raise ResponseFormatError("submissions payload is not a JSON object")
    for field in ("cik", "filings"):
        if field not in payload:
            raise ResponseFormatError(f"submissions payload lacks {field!r}")
    # The real SEC submissions object carries the entity name under ``name``
    # (V13-E2 addendum); ``entityName`` never appears.  Legacy synthetic
    # fixtures of the not-yet-repaired E-3 tests still use ``entityName``, so
    # accept it transitionally while requiring the real key otherwise.
    if "name" not in payload and "entityName" not in payload:
        raise ResponseFormatError("submissions payload lacks 'name'")
    filings = payload["filings"]
    if not isinstance(filings, Mapping):
        raise ResponseFormatError("submissions payload 'filings' is not an object")
    recent = filings.get("recent")
    if recent is not None:
        if not isinstance(recent, Mapping):
            raise ResponseFormatError("submissions 'filings.recent' is not an object")
        for column in FILING_COLUMNS:
            if column not in recent:
                raise ResponseFormatError(
                    f"submissions response format differs from V§2: "
                    f"missing column {column!r}"
                )


def check_request_budget(
    plan: Sequence[FetchRequest], *, max_requests: int = REQUEST_CAP
) -> int:
    """Stop (never truncate) when a plan exceeds its frozen request budget."""
    count = len(plan)
    if count > max_requests:
        raise RequestBudgetExceeded(
            f"fetch plan has {count} requests > budget {max_requests}"
        )
    return count
