"""Discovery Dataset v1 — resumable Tiingo EOD acquisition (D2).

Frozen contract: ``docs/discovery_dataset_v1_contract.md`` (SHA-256
``d03f3554…80e3``), governing sections 7, 13, 14, 15, 16, 19, 20, 21 and 22.

This module is the bounded, resumable acquisition mechanism for the
per-ticker Tiingo EOD range. It is deliberately *not* allowed to start any
acquisition on import: every network touch goes through an injected
:data:`Transport`, and the only live transport is built by
:func:`make_live_transport` and given a finite timeout.

Design boundaries
-----------------
* **No live provider call in implementation or tests.** The tests always
  inject a fake transport; the live transport is exercised only with
  ``urllib.request.urlopen`` monkeypatched.
* **No D1 import.** The candidate-pool builder is never imported; the
  expected-pool artifact is consumed purely as data.
* **No vendor-client edit.** ``smart_beta/vendors/tiingo/client.py`` is
  imported only for its ``BASE_URL`` (and its bytes are hashed for the
  manifest adapter-code hash), never modified.
* **No credential in any artifact.** The API key is read at call time from
  ``TIINGO_API_KEY`` and placed only in the ``Authorization`` header; it is
  never put in a URL, an exception message, a manifest record or stdout.

The storage layout mirrors contract §14::

    <root>/raw/<batch_id>/<ticker>.json.gz
    <root>/manifest/<batch_id>/batch.json
    <root>/manifest/<batch_id>/attempts.jsonl
    <root>/manifest/<batch_id>/status.json
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import http.client
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping, Sequence

from smart_beta.vendors.tiingo import client as tiingo_client

__all__ = [
    "CONTRACT_SHA256",
    "EXPECTED_POOL_SCHEMA",
    "MANIFEST_SCHEMA_VERSION",
    "DEFAULT_ROOT",
    "TICKER_PATTERN",
    "MAX_ATTEMPTS",
    "TERMINAL_STATUSES",
    "BATCH_STATES",
    "DatasetV1Error",
    "ExpectedPoolError",
    "TickerValidationError",
    "UnrequestableIdentifierError",
    "TransportError",
    "ConfigError",
    "ManifestError",
    "Classification",
    "canonical_json",
    "sha256_hex",
    "gzip_bytes",
    "read_expected_pool",
    "compute_batch_id",
    "create_batch",
    "run_batch",
    "refetch",
    "classify_body",
    "validate_ticker",
    "is_requestable_ticker",
    "eod_request_path",
    "eod_request_params",
    "make_live_transport",
    "utc_now",
]

#: The frozen contract hash this implementation is bound to.
CONTRACT_SHA256 = (
    "d03f35549777e5a94046f06dd0621acfc3a1dedb7a4b40adc55fdae911fa80e3"
)

#: The expected-pool artifact schema produced by D1.
EXPECTED_POOL_SCHEMA = "discovery-dataset-v1-expected-pool-v1"

#: Manifest schema version recorded in the batch header.
MANIFEST_SCHEMA_VERSION = "discovery-dataset-v1-manifest-v1"

#: Default acquisition root (contract §14). Tests always pass ``tmp_path``.
DEFAULT_ROOT = os.path.expanduser(
    "~/Developer/personal/smart_beta/data_store/discovery_dataset_v1"
)

#: Environment variable holding the Tiingo API key.
API_KEY_ENV_VAR = "TIINGO_API_KEY"

#: A ticker must be an uppercase alphanumeric run, optionally containing
#: ``.`` or ``-`` but never starting with either.
TICKER_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9.\-]*$")

#: Reason code recorded for an identifier the request path cannot represent.
REQUEST_PATTERN_MISMATCH = "REQUEST_PATTERN_MISMATCH"

#: At most three attempts per request (contract §15).
MAX_ATTEMPTS = 3

#: Bounded exponential backoff.
BACKOFF_BASE_S = 1.0
MAX_BACKOFF_S = 30.0

#: Live HTTP timeout. Finite, as required.
DEFAULT_TIMEOUT_S = 30.0

#: Required keys for an EOD row to classify as ``OK`` (contract §7).
REQUIRED_EOD_KEYS = ("date", "close", "volume", "divCash", "splitFactor")

#: Header used for the API key.
AUTH_HEADER_NAME = "Authorization"

#: Terminal ticker statuses (contract §15). ``PENDING`` is non-terminal.
#: ``UNREQUESTABLE_IDENTIFIER`` is terminal for batch completion but is not a
#: request, not OK, not EMPTY and not a provider failure.
TERMINAL_STATUSES = frozenset(
    {
        "OK",
        "EMPTY",
        "MALFORMED",
        "ENTITLEMENT_ERROR",
        "RETRY_EXHAUSTED",
        "NONDETERMINISTIC",
        "UNREQUESTABLE_IDENTIFIER",
    }
)

#: Batch states.
BATCH_STATES = frozenset({"RUNNING", "COMPLETE", "QUOTA_EXHAUSTED", "INTERRUPTED"})

#: Wire-level HTTP failures that mean a response body or status line was
#: truncated, not that the request was malformed. ``IncompleteRead`` (a body
#: cut short), ``BadStatusLine`` (an unparseable status line, which includes
#: ``RemoteDisconnected``) and ``LineTooLong`` (an over-long header line) are
#: all genuine, retryable transport failures. They are ``HTTPException``
#: subclasses, *not* ``OSError``, so before D2-D they escaped the retry path
#: entirely, crashed the run and left no attempt record. Only these three are
#: caught: the parent ``http.client.HTTPException`` is deliberately *not*
#: caught wholesale because its other subclasses (``InvalidURL``,
#: ``UnknownProtocol``, ``UnknownTransferEncoding``, ``UnimplementedFileMode``,
#: ``NotConnected``, ``ImproperConnectionState`` …) indicate programming or
#: configuration errors, not transient network failures, and must keep
#: propagating (task D2-D, required semantics 1).
_HTTP_CLIENT_TRANSPORT_ERRORS = (
    http.client.IncompleteRead,
    http.client.BadStatusLine,
    http.client.LineTooLong,
)

#: ``(path, params, timeout_s) -> (http_status, raw_bytes)``.
Transport = Callable[[str, Mapping[str, str], float], "tuple[int, bytes]"]

#: ``() -> ISO-8601 UTC string``.
Clock = Callable[[], str]

#: ``seconds -> None``.
Sleeper = Callable[[float], None]


# ---------------------------------------------------------------------------
# Typed errors
# ---------------------------------------------------------------------------
class DatasetV1Error(Exception):
    """Base class for every D2 fail-closed error."""


class ExpectedPoolError(DatasetV1Error):
    """The expected-pool artifact is missing, malformed or non-conforming."""


class TickerValidationError(DatasetV1Error):
    """A ticker failed the ``^[A-Z0-9][A-Z0-9.\\-]*$`` pattern.

    Raised before any path is constructed, so an invalid ticker can never
    reach the filesystem.
    """


class UnrequestableIdentifierError(DatasetV1Error):
    """A frozen D1 pool identifier the D2 request path cannot represent.

    This is strictly a request-path limitation. It carries no security-type
    meaning: it does not mean the identifier is invalid, non-common, a
    warrant, preferred, note or ineligible.
    """


class TransportError(DatasetV1Error):
    """A retryable transport failure (timeout or connection error).

    The message never carries a credential; the key lives only in the
    request header, outside this exception.
    """


class ConfigError(DatasetV1Error):
    """No API key is available for a live request."""


class ManifestError(DatasetV1Error):
    """A batch header or status file is missing, unreadable or inconsistent."""


# ---------------------------------------------------------------------------
# Canonical JSON / hashing / storage primitives
# ---------------------------------------------------------------------------
def canonical_json(obj: object) -> str:
    """The frozen canonical JSON spelling (plan interface, contract §19)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sha256_hex(data: bytes) -> str:
    """Lowercase hex SHA-256 of ``data``."""
    return hashlib.sha256(data).hexdigest()


def gzip_bytes(raw: bytes) -> bytes:
    """Deterministic gzip of ``raw`` (``mtime=0``, empty filename field)."""
    return gzip.compress(raw, compresslevel=9, mtime=0)


def _file_sha256(path: Path) -> str:
    return sha256_hex(Path(path).read_bytes())


def _atomic_write(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` atomically (temp file + replace)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def _write_new(path: Path, data: bytes) -> None:
    """Create ``path`` exclusively; never overwrite an existing raw file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "xb") as handle:
        handle.write(data)


def _write_canonical_json(path: Path, obj: object) -> bytes:
    data = (canonical_json(obj) + "\n").encode("utf-8")
    _atomic_write(path, data)
    return data


def utc_now() -> str:
    """Current UTC instant as an ISO-8601 ``…Z`` string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Ticker validation and request construction
# ---------------------------------------------------------------------------
def is_requestable_ticker(ticker: object) -> bool:
    """True iff ``ticker`` can be safely represented on the request path.

    Purely a lexical request-path check. A ``False`` result says nothing
    about the identifier's security type, validity or eligibility.
    """
    return isinstance(ticker, str) and bool(TICKER_PATTERN.match(ticker))


def validate_ticker(ticker: str) -> str:
    """Return ``ticker`` if it is well formed, else raise.

    This runs before any path is built, so a rejected ticker constructs no
    path and touches no filesystem entry.
    """
    if not is_requestable_ticker(ticker):
        raise TickerValidationError(
            f"invalid ticker {ticker!r}: must match {TICKER_PATTERN.pattern}"
        )
    return ticker


def eod_request_path(ticker: str) -> str:
    """The endpoint path, built exactly as ``TiingoClient.get_eod_prices``."""
    return f"/tiingo/daily/{urllib.parse.quote(ticker)}/prices"


def eod_request_params(acquisition_start: str, c: str) -> dict[str, str]:
    """The query params ``TiingoClient.get_eod_prices`` sends."""
    return {"startDate": acquisition_start, "endDate": c}


# ---------------------------------------------------------------------------
# Expected-pool verification
# ---------------------------------------------------------------------------
def read_expected_pool(
    pool_path: str | os.PathLike[str],
    *,
    expected_pool_sha256: str | None = None,
) -> tuple[dict, bytes, str]:
    """Read and fail-closed-verify the expected-pool artifact.

    Verifies, in order:

    1. the bytes parse as JSON and are byte-canonical (UTF-8 canonical JSON
       plus a trailing newline), which is what makes the file's SHA-256 a
       meaningful integrity binding;
    2. ``schema`` equals :data:`EXPECTED_POOL_SCHEMA`;
    3. ``contract_sha256`` equals :data:`CONTRACT_SHA256`;
    4. if ``expected_pool_sha256`` is supplied, the file's SHA-256 matches it.

    Returns ``(pool_dict, raw_bytes, file_sha256)``.
    """
    path = Path(pool_path)
    try:
        raw = path.read_bytes()
    except OSError as exc:  # pragma: no cover - exercised via typed error test
        raise ExpectedPoolError(f"cannot read expected-pool artifact {path}: {exc}") from exc

    digest = sha256_hex(raw)

    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ExpectedPoolError(
            f"expected-pool artifact {path} is not valid UTF-8 JSON"
        ) from exc

    if not isinstance(parsed, dict):
        raise ExpectedPoolError("expected-pool artifact must be a JSON object")

    canonical = (canonical_json(parsed) + "\n").encode("utf-8")
    if canonical != raw:
        raise ExpectedPoolError(
            "expected-pool artifact is not canonical JSON (byte spelling differs)"
        )

    if parsed.get("schema") != EXPECTED_POOL_SCHEMA:
        raise ExpectedPoolError(
            f"unexpected expected-pool schema {parsed.get('schema')!r}; "
            f"expected {EXPECTED_POOL_SCHEMA!r}"
        )

    if parsed.get("contract_sha256") != CONTRACT_SHA256:
        raise ExpectedPoolError(
            "expected-pool contract_sha256 does not match the frozen contract"
        )

    if expected_pool_sha256 is not None and digest != expected_pool_sha256:
        raise ExpectedPoolError(
            "expected-pool file SHA-256 does not match the caller-supplied value"
        )

    return parsed, raw, digest


# ---------------------------------------------------------------------------
# batch_id
# ---------------------------------------------------------------------------
def compute_batch_id(
    *,
    contract_sha256: str,
    snapshot_sha256: str,
    expected_pool_sha256: str,
    c: str,
    created_at_utc: str,
) -> str:
    """``"b-" + sha256(canonical_json({…}))[:16]`` (task §8)."""
    payload = canonical_json(
        {
            "contract_sha256": contract_sha256,
            "snapshot_sha256": snapshot_sha256,
            "expected_pool_sha256": expected_pool_sha256,
            "C": c,
            "created_at_utc": created_at_utc,
        }
    )
    return "b-" + sha256_hex(payload.encode("utf-8"))[:16]


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
def _raw_dir(root: str | os.PathLike[str], batch_id: str) -> Path:
    return Path(root) / "raw" / batch_id


def _manifest_dir(root: str | os.PathLike[str], batch_id: str) -> Path:
    return Path(root) / "manifest" / batch_id


def _raw_path(
    root: str | os.PathLike[str], batch_id: str, ticker: str, filename: str
) -> Path:
    """Build a raw path; validates ``ticker`` first (no path on failure)."""
    validate_ticker(ticker)
    return _raw_dir(root, batch_id) / filename


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Classification:
    """Result of classifying a 200 response body."""

    status: str
    row_count: int | None
    first_date: str | None
    last_date: str | None


def classify_body(raw: bytes) -> Classification:
    """Classify a **200** body (task §4).

    * empty JSON list → ``EMPTY``;
    * every element an object with ``date``, ``close``, ``volume``,
      ``divCash`` and ``splitFactor`` → ``OK`` (row count + first/last date);
    * anything else, including an unparseable/truncated body → ``MALFORMED``.
    """
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return Classification("MALFORMED", None, None, None)

    if not isinstance(parsed, list):
        return Classification("MALFORMED", None, None, None)

    if not parsed:
        return Classification("EMPTY", 0, None, None)

    for row in parsed:
        if not isinstance(row, dict) or any(
            key not in row for key in REQUIRED_EOD_KEYS
        ):
            return Classification("MALFORMED", None, None, None)

    return Classification(
        "OK",
        len(parsed),
        parsed[0].get("date"),
        parsed[-1].get("date"),
    )


# ---------------------------------------------------------------------------
# Batch header / status persistence
# ---------------------------------------------------------------------------
def _load_header(root: str | os.PathLike[str], batch_id: str) -> dict:
    path = _manifest_dir(root, batch_id) / "batch.json"
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, ValueError) as exc:
        raise ManifestError(f"cannot read batch header {path}: {exc}") from exc


def _load_status(root: str | os.PathLike[str], batch_id: str) -> dict:
    path = _manifest_dir(root, batch_id) / "status.json"
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, ValueError) as exc:
        raise ManifestError(f"cannot read batch status {path}: {exc}") from exc


def _write_status(root: str | os.PathLike[str], batch_id: str, status: dict) -> tuple[bytes, str]:
    path = _manifest_dir(root, batch_id) / "status.json"
    data = _write_canonical_json(path, status)
    return data, sha256_hex(data)


def _append_attempt(root: str | os.PathLike[str], batch_id: str, record: dict) -> None:
    path = _manifest_dir(root, batch_id) / "attempts.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    line = (canonical_json(record) + "\n").encode("utf-8")
    with open(path, "ab") as handle:
        handle.write(line)


def _new_status(header: dict) -> dict:
    """Initial status for every pool identifier (never drops a ticker).

    A requestable identifier starts ``PENDING``. An unrequestable identifier
    starts ``UNREQUESTABLE_IDENTIFIER`` with no response fields, because no
    request will ever be made for it.
    """
    tickers: dict[str, dict] = {}
    for entry in header["tickers"]:
        if entry.get("requestable", True):
            tickers[entry["ticker"]] = {"status": "PENDING", "attempts": 0}
        else:
            tickers[entry["ticker"]] = {
                "status": "UNREQUESTABLE_IDENTIFIER",
                "reason_code": REQUEST_PATTERN_MISMATCH,
                "request_pattern": TICKER_PATTERN.pattern,
                "attempts": 0,
            }
    return {
        "batch_id": header["batch_id"],
        "manifest_schema_version": header["manifest_schema_version"],
        "contract_sha256": header["contract_sha256"],
        "C": header["C"],
        "batch_state": "RUNNING",
        "tickers": tickers,
    }


# ---------------------------------------------------------------------------
# create_batch
# ---------------------------------------------------------------------------
def create_batch(
    pool_path: str | os.PathLike[str],
    root: str | os.PathLike[str],
    clock: Clock,
    *,
    expected_pool_sha256: str | None = None,
) -> str:
    """Create a new batch header from a verified expected-pool artifact.

    The ``batch_id`` is computed once from the contract/snapshot/pool hashes,
    ``C`` and the injectable clock, persisted in the header and reused on
    every resume. Returns the ``batch_id``.

    Every pool identifier is kept verbatim and tagged with ``requestable``.
    An identifier that fails :data:`TICKER_PATTERN` is recorded as
    ``requestable: false`` and starts in the ``UNREQUESTABLE_IDENTIFIER``
    terminal status; it never aborts the batch.
    """
    pool, _raw, pool_sha = read_expected_pool(
        pool_path, expected_pool_sha256=expected_pool_sha256
    )

    created_at_utc = clock()
    batch_id = compute_batch_id(
        contract_sha256=pool["contract_sha256"],
        snapshot_sha256=pool["snapshot_sha256"],
        expected_pool_sha256=pool_sha,
        c=pool["C"],
        created_at_utc=created_at_utc,
    )

    entries = []
    for row in pool.get("tickers", []):
        ticker = row["ticker"]
        intervals = row.get("listed_intervals", [])
        entries.append(
            {
                "ticker": ticker,
                "security_keys": [
                    interval["security_key"] for interval in intervals
                ],
                "requestable": is_requestable_ticker(ticker),
            }
        )
    entries.sort(key=lambda entry: entry["ticker"])

    header = {
        "batch_id": batch_id,
        "contract_sha256": pool["contract_sha256"],
        "snapshot_sha256": pool["snapshot_sha256"],
        "expected_pool_sha256": pool_sha,
        "acquisition_start": pool["acquisition_start"],
        "C": pool["C"],
        "created_at_utc": created_at_utc,
        "acquisition_tool_sha256": _file_sha256(Path(__file__)),
        "adapter_code_sha256": _file_sha256(Path(tiingo_client.__file__)),
        "manifest_schema_version": MANIFEST_SCHEMA_VERSION,
        "pool_path": str(Path(pool_path).resolve()),
        "tickers": entries,
    }

    manifest_dir = _manifest_dir(root, batch_id)
    manifest_dir.mkdir(parents=True, exist_ok=True)
    # The header is created once and reused on resume: re-creating the same
    # batch must never reset recorded progress.
    if not (manifest_dir / "batch.json").exists():
        _write_canonical_json(manifest_dir / "batch.json", header)
    if not (manifest_dir / "status.json").exists():
        _write_status(root, batch_id, _new_status(header))
    return batch_id


# ---------------------------------------------------------------------------
# Raw storage
# ---------------------------------------------------------------------------
def _store_raw(
    root: str | os.PathLike[str],
    batch_id: str,
    ticker: str,
    raw: bytes,
) -> tuple[str, str]:
    """Store exact provider bytes immutably; return ``(filename, sha256)``.

    The base file ``<ticker>.json.gz`` is created at most once. A later body
    that differs is written as ``<ticker>.refetch-<n>.json.gz`` so both copies
    are kept. Never overwrites.
    """
    validate_ticker(ticker)
    data = gzip_bytes(raw)
    base_name = f"{ticker}.json.gz"
    base = _raw_path(root, batch_id, ticker, base_name)
    if not base.exists():
        _write_new(base, data)
        return base_name, sha256_hex(raw)
    if base.read_bytes() == data:
        return base_name, sha256_hex(raw)
    n = 1
    while True:
        name = f"{ticker}.refetch-{n}.json.gz"
        candidate = _raw_path(root, batch_id, ticker, name)
        if not candidate.exists():
            _write_new(candidate, data)
            return name, sha256_hex(raw)
        n += 1


def _verify_ok_file(root: str | os.PathLike[str], batch_id: str, record: dict) -> bool:
    """True iff a stored ``OK`` file re-hashes to its manifest SHA-256."""
    filename = record.get("raw_file")
    expected = record.get("raw_sha256")
    if not filename or not expected:
        return False
    path = _raw_dir(root, batch_id) / filename
    try:
        raw = gzip.decompress(path.read_bytes())
    except Exception:
        # Any unreadable/undecodable stored copy is a corruption event, never
        # an interrupt: gzip can raise OSError, EOFError or zlib.error.
        return False
    return sha256_hex(raw) == expected


# ---------------------------------------------------------------------------
# Retryable-error detection
# ---------------------------------------------------------------------------
_RETRYABLE = (TransportError, TimeoutError, OSError, *_HTTP_CLIENT_TRANSPORT_ERRORS)


def _backoff(attempt: int) -> float:
    """Exponential backoff, bounded by :data:`MAX_BACKOFF_S`."""
    return min(BACKOFF_BASE_S * (2 ** (attempt - 1)), MAX_BACKOFF_S)


class _StopBatch(Exception):
    """Internal signal: stop cleanly, leaving the current ticker pending."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class _Budget:
    """A hard cap on transport calls for a bounded run."""

    def __init__(self, limit: int | None) -> None:
        self.limit = limit
        self.calls = 0

    def spend(self) -> None:
        if self.limit is not None and self.calls >= self.limit:
            raise _StopBatch("max_requests")
        self.calls += 1


@dataclass
class _Outcome:
    kind: str
    attempts: int
    raw_sha256: str | None = None
    raw_file: str | None = None
    row_count: int | None = None
    first_date: str | None = None
    last_date: str | None = None
    retrieved_at_utc: str | None = None


def _perform_request(
    *,
    ticker: str,
    security_keys: Sequence[str],
    header: dict,
    root: str | os.PathLike[str],
    transport: Transport,
    sleep: Sleeper,
    clock: Clock,
    timeout_s: float,
    budget: _Budget,
) -> _Outcome:
    """Run one ticker's request with retry/backoff (task §5)."""
    batch_id = header["batch_id"]
    path = eod_request_path(ticker)
    params = eod_request_params(header["acquisition_start"], header["C"])

    attempts = 0
    while attempts < MAX_ATTEMPTS:
        attempts += 1
        retrieved_at = clock()
        try:
            budget.spend()
            status_code, raw = transport(path, params, timeout_s)
        except _StopBatch:
            raise
        except _RETRYABLE as exc:
            # A wire-level truncation may reach us already wrapped in a
            # TransportError by the live transport (with the concrete cause in
            # __cause__) or, under an injected transport, as the raw
            # http.client exception. Both spell the same transport failure, so
            # a raw one is normalised to error_class "TransportError" with its
            # own class name in transport_cause.
            if isinstance(exc, _HTTP_CLIENT_TRANSPORT_ERRORS):
                error_class = "TransportError"
                cause_name = type(exc).__name__
            else:
                error_class = type(exc).__name__
                cause_name = (
                    type(exc.__cause__).__name__
                    if exc.__cause__ is not None
                    else type(exc).__name__
                )
            _append_attempt(
                root,
                batch_id,
                {
                    "batch_id": batch_id,
                    "ticker": ticker,
                    "security_keys": list(security_keys),
                    "endpoint": path,
                    "requested_range": dict(params),
                    "retrieved_at_utc": retrieved_at,
                    "http_status": None,
                    "error_class": error_class,
                    "transport_cause": cause_name,
                    "attempt": attempts,
                },
            )
            if attempts >= MAX_ATTEMPTS:
                return _Outcome("RETRY_EXHAUSTED", attempts, retrieved_at_utc=retrieved_at)
            sleep(_backoff(attempts))
            continue

        if status_code == 200:
            classification = classify_body(raw)
            raw_sha = sha256_hex(raw)
            raw_file, _ = _store_raw(root, batch_id, ticker, raw)
            _append_attempt(
                root,
                batch_id,
                {
                    "batch_id": batch_id,
                    "ticker": ticker,
                    "security_keys": list(security_keys),
                    "endpoint": path,
                    "requested_range": dict(params),
                    "retrieved_at_utc": retrieved_at,
                    "http_status": 200,
                    "error_class": None,
                    "attempt": attempts,
                    "raw_sha256": raw_sha,
                    "row_count": classification.row_count,
                    "first_date": classification.first_date,
                    "last_date": classification.last_date,
                },
            )
            return _Outcome(
                classification.status,
                attempts,
                raw_sha256=raw_sha,
                raw_file=raw_file,
                row_count=classification.row_count,
                first_date=classification.first_date,
                last_date=classification.last_date,
                retrieved_at_utc=retrieved_at,
            )

        if 500 <= status_code <= 599:
            kind = "RETRY_EXHAUSTED"
        elif status_code == 429:
            kind = "QUOTA_EXHAUSTED"
        elif 400 <= status_code <= 499:
            kind = "ENTITLEMENT_ERROR"
        else:
            kind = "RETRY_EXHAUSTED"

        _append_attempt(
            root,
            batch_id,
            {
                "batch_id": batch_id,
                "ticker": ticker,
                "security_keys": list(security_keys),
                "endpoint": path,
                "requested_range": dict(params),
                "retrieved_at_utc": retrieved_at,
                # Provider failures keep a non-null http_status and carry no
                # transport_cause; a failed attempt never records response
                # fields (raw hash, row count, first/last date).
                "http_status": status_code,
                "error_class": None,
                "attempt": attempts,
            },
        )

        if kind == "ENTITLEMENT_ERROR":
            return _Outcome(kind, attempts, retrieved_at_utc=retrieved_at)
        if attempts >= MAX_ATTEMPTS:
            return _Outcome(kind, attempts, retrieved_at_utc=retrieved_at)
        sleep(_backoff(attempts))

    # Unreachable: the loop always returns on the final attempt.
    return _Outcome("RETRY_EXHAUSTED", attempts)  # pragma: no cover


# ---------------------------------------------------------------------------
# run_batch / refetch
# ---------------------------------------------------------------------------
def _run(
    batch_id: str,
    root: str | os.PathLike[str],
    transport: Transport,
    sleep: Sleeper,
    clock: Clock,
    max_requests: int | None,
    *,
    refetch_tickers: Sequence[str] | None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> dict:
    header = _load_header(root, batch_id)
    if header.get("batch_id") != batch_id:
        raise ManifestError("batch header batch_id does not match the directory")
    status = _load_status(root, batch_id)
    entries = {entry["ticker"]: entry for entry in header["tickers"]}
    budget = _Budget(max_requests)
    refetch = refetch_tickers is not None

    def persist() -> str:
        _data, digest = _write_status(root, batch_id, status)
        return digest

    stop: str | None = None
    try:
        for ticker in sorted(status["tickers"]):
            record = status["tickers"][ticker]
            current = record.get("status", "PENDING")

            if refetch:
                if ticker not in refetch_tickers:
                    continue
            else:
                if current in TERMINAL_STATUSES:
                    if current == "OK" and not _verify_ok_file(root, batch_id, record):
                        # Corrupt local copy: refetch it, keep the event visible.
                        record["status"] = "PENDING"
                        record["last_error"] = "CORRUPT_LOCAL"
                        _append_attempt(
                            root,
                            batch_id,
                            {
                                "batch_id": batch_id,
                                "ticker": ticker,
                                "security_keys": list(entries[ticker]["security_keys"]),
                                "endpoint": eod_request_path(ticker),
                                "requested_range": eod_request_params(
                                    header["acquisition_start"], header["C"]
                                ),
                                "retrieved_at_utc": clock(),
                                "http_status": None,
                                "error_class": "CORRUPT_LOCAL",
                                "attempt": 0,
                                "raw_sha256": None,
                                "row_count": None,
                                "first_date": None,
                                "last_date": None,
                            },
                        )
                        persist()
                    else:
                        continue

            prior_status = record.get("status")
            prior_sha = record.get("raw_sha256")
            prior_file = record.get("raw_file")

            try:
                outcome = _perform_request(
                    ticker=ticker,
                    security_keys=entries[ticker]["security_keys"],
                    header=header,
                    root=root,
                    transport=transport,
                    sleep=sleep,
                    clock=clock,
                    timeout_s=timeout_s,
                    budget=budget,
                )
            except _StopBatch:
                stop = "max_requests"
                break

            record["attempts"] = record.get("attempts", 0) + outcome.attempts

            if outcome.kind == "QUOTA_EXHAUSTED":
                # Ticker stays PENDING; the batch stops cleanly and resumes.
                # In refetch mode a previously verified OK record is untouched.
                if refetch and prior_status == "OK":
                    record["last_refetch_error"] = "QUOTA_EXHAUSTED"
                elif outcome.retrieved_at_utc is not None:
                    record["retrieved_at_utc"] = outcome.retrieved_at_utc
                persist()
                stop = "quota"
                break

            if outcome.kind in TERMINAL_STATUSES:
                is_200 = outcome.kind in {"OK", "EMPTY", "MALFORMED"}
                if refetch and prior_status == "OK" and not is_200:
                    # A failed refetch must never downgrade or drop a verified
                    # OK record: keep the status and the original file/hash,
                    # and make the failure visible on the record (the
                    # attempts.jsonl lines are already written).
                    record["status"] = "OK"
                    record["raw_file"] = prior_file
                    record["raw_sha256"] = prior_sha
                    record["last_refetch_error"] = outcome.kind
                else:
                    if outcome.retrieved_at_utc is not None:
                        record["retrieved_at_utc"] = outcome.retrieved_at_utc
                    if (
                        refetch
                        and prior_status == "OK"
                        and outcome.raw_sha256 == prior_sha
                    ):
                        record["status"] = "OK"
                        record["raw_file"] = prior_file
                        record["raw_sha256"] = prior_sha
                        record.pop("last_refetch_error", None)
                    elif refetch and prior_status == "OK":
                        record["status"] = "NONDETERMINISTIC"
                        record["raw_file"] = outcome.raw_file
                        record["raw_sha256"] = outcome.raw_sha256
                        record["previous_raw_file"] = prior_file
                        record["previous_raw_sha256"] = prior_sha
                        record.pop("last_refetch_error", None)
                    else:
                        record["status"] = outcome.kind
                        record["raw_file"] = outcome.raw_file
                        record["raw_sha256"] = outcome.raw_sha256
                if outcome.row_count is not None:
                    record["row_count"] = outcome.row_count
                if outcome.first_date is not None:
                    record["first_date"] = outcome.first_date
                if outcome.last_date is not None:
                    record["last_date"] = outcome.last_date
                record.pop("last_error", None)
            persist()
    except BaseException:
        # Covers ordinary exceptions and signals alike: already-written records
        # stay intact, the batch is marked resumable, and the cause propagates.
        status["batch_state"] = "INTERRUPTED"
        persist()
        raise

    if stop == "quota":
        state = "QUOTA_EXHAUSTED"
    elif stop == "max_requests":
        state = "INTERRUPTED"
    elif all(
        record.get("status") in TERMINAL_STATUSES
        for record in status["tickers"].values()
    ):
        state = "COMPLETE"
    else:
        state = "INTERRUPTED"

    status["batch_state"] = state
    manifest_sha = persist()

    counts: dict[str, int] = {}
    for record in status["tickers"].values():
        key = record.get("status", "PENDING")
        counts[key] = counts.get(key, 0) + 1

    return {
        "batch_id": batch_id,
        "batch_state": state,
        "manifest_sha256": manifest_sha,
        "requests_made": budget.calls,
        "counts": counts,
        "tickers": status["tickers"],
    }


def run_batch(
    batch_id: str,
    root: str | os.PathLike[str],
    transport: Transport,
    sleep: Sleeper,
    clock: Clock,
    max_requests: int | None = None,
) -> dict:
    """Resume/complete a batch. Returns a summary dict.

    A verified ``OK`` ticker is skipped (zero transport calls). ``max_requests``
    caps transport calls; hitting it stops the batch cleanly (state
    ``INTERRUPTED``, remaining tickers ``PENDING``).
    """
    return _run(
        batch_id,
        root,
        transport,
        sleep,
        clock,
        max_requests,
        refetch_tickers=None,
    )


def refetch(
    batch_id: str,
    root: str | os.PathLike[str],
    transport: Transport,
    sleep: Sleeper,
    clock: Clock,
    tickers: Sequence[str] | str | None = None,
    max_requests: int | None = None,
) -> dict:
    """Explicitly re-fetch ``OK`` tickers (task §7).

    ``tickers`` defaults to every ``OK`` ticker. A differing body keeps both
    files and sets ``NONDETERMINISTIC``; an identical body keeps ``OK``.
    """
    header = _load_header(root, batch_id)
    status = _load_status(root, batch_id)
    if tickers is None:
        selected = [
            ticker
            for ticker, record in status["tickers"].items()
            if record.get("status") == "OK"
        ]
    elif isinstance(tickers, str):
        selected = [tickers]
    else:
        selected = list(tickers)
    for ticker in selected:
        if ticker not in status["tickers"]:
            raise ManifestError(f"ticker {ticker!r} is not in batch {batch_id}")
        if status["tickers"][ticker].get("status") == "UNREQUESTABLE_IDENTIFIER":
            raise UnrequestableIdentifierError(
                f"ticker {ticker!r} is UNREQUESTABLE_IDENTIFIER and cannot be "
                "requested; no state was changed"
            )
        validate_ticker(ticker)
    return _run(
        batch_id,
        root,
        transport,
        sleep,
        clock,
        max_requests,
        refetch_tickers=set(selected),
    )


# ---------------------------------------------------------------------------
# Live transport
# ---------------------------------------------------------------------------
def make_live_transport(
    api_key: str | None = None,
    *,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> Transport:
    """Build the live ``urllib.request`` transport.

    The key is read at call time from ``TIINGO_API_KEY`` unless one was
    passed explicitly. It is placed only in the ``Authorization`` header and
    never logged, never placed in the URL, and never attached to an
    exception.
    """

    def transport(
        path: str, params: Mapping[str, str], timeout_s_arg: float
    ) -> tuple[int, bytes]:
        key = api_key if api_key is not None else os.environ.get(API_KEY_ENV_VAR)
        if not key:
            raise ConfigError(
                f"no Tiingo API key: set {API_KEY_ENV_VAR} or pass one explicitly"
            )

        url = tiingo_client.BASE_URL + path
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"

        request = urllib.request.Request(url, method="GET")
        request.add_header(AUTH_HEADER_NAME, f"Token {key}")

        effective_timeout = timeout_s_arg or timeout_s
        try:
            with urllib.request.urlopen(request, timeout=effective_timeout) as response:
                return int(response.status), response.read()
        except urllib.error.HTTPError as exc:
            # ``exc.read()`` can truncate exactly like ``response.read()``.
            try:
                return int(exc.code), exc.read()
            except _HTTP_CLIENT_TRANSPORT_ERRORS as read_exc:
                # Never interpolate the request headers or the key here; the
                # concrete class name is the only detail that escapes.
                raise TransportError(
                    f"transport failure for {path}: {type(read_exc).__name__}"
                ) from read_exc
        except _HTTP_CLIENT_TRANSPORT_ERRORS as exc:
            # A truncated body/status line is a retryable transport failure.
            # Catch only the three classes above; a programming or
            # configuration error (e.g. ``InvalidURL``) must keep propagating.
            raise TransportError(
                f"transport failure for {path}: {type(exc).__name__}"
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            # Never interpolate the request headers or the key here.
            raise TransportError(
                f"transport failure for {path}: {type(exc).__name__}"
            ) from exc

    return transport


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="smart_beta.dataset_v1.acquisition",
        description=(
            "Discovery Dataset v1 resumable acquisition tool. Touching the "
            "network requires BOTH --live and --max-requests."
        ),
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="required to issue any live Tiingo request",
    )
    parser.add_argument(
        "--max-requests",
        type=int,
        default=None,
        help="hard cap on transport calls; required together with --live",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create", help="create a batch header (offline)")
    create.add_argument("--pool", required=True, help="expected-pool JSON path")
    create.add_argument("--root", default=DEFAULT_ROOT)

    run = sub.add_parser("run", help="resume/complete a batch (live only)")
    run.add_argument("--batch-id", required=True)
    run.add_argument("--root", default=DEFAULT_ROOT)

    refetch_p = sub.add_parser("refetch", help="explicitly re-fetch OK tickers")
    refetch_p.add_argument("--batch-id", required=True)
    refetch_p.add_argument("--tickers", nargs="*", default=None)
    refetch_p.add_argument("--root", default=DEFAULT_ROOT)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "create":
        batch_id = create_batch(args.pool, args.root, utc_now)
        print(batch_id)
        return 0

    if not args.live:
        print(
            "error: refusing to touch the network without --live",
            file=sys.stderr,
        )
        return 2
    if args.max_requests is None:
        print(
            "error: --max-requests is required together with --live",
            file=sys.stderr,
        )
        return 2

    transport = make_live_transport()
    sleep = time.sleep
    if args.command == "run":
        summary = run_batch(
            args.batch_id,
            args.root,
            transport,
            sleep,
            utc_now,
            max_requests=args.max_requests,
        )
    else:
        summary = refetch(
            args.batch_id,
            args.root,
            transport,
            sleep,
            utc_now,
            tickers=args.tickers,
            max_requests=args.max_requests,
        )
    print(canonical_json(summary))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
