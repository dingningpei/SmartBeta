#!/usr/bin/env python3
"""Generic, transport-agnostic staged/resumable capture framework (P5B-5).

This module is a **fresh, independent extraction of the pattern P5A-4 proved
in ``scripts/fetch_phase5a_gate_b_fixtures.py``**. That Phase 5A recorder is
part of Phase 5A's sealed merge set and is therefore neither imported nor
modified here; it was used only as a read reference. This file ships no
network client and performs no I/O beyond the local staging/canonical
directories it is pointed at: the caller injects a ``transport`` callable.

Guarantees implemented (the frozen Phase 5B "Rate-limit-aware capture"
contract, ``worker_tasks/phase5b/phase5b-plan.md``):

* **Deterministic per-request identities** -- a capture's identity is a pure
  function of its ``(endpoint, params)`` request, never of wall-clock time,
  iteration order, or list position, so the same logical request always names
  the same staged artifact across separate invocations.
* **Staging ledger** -- every staged success lands in a non-final staging area
  with full provenance (identity, endpoint, params, retrieval timestamp,
  status, canonical content hash, raw body) and a ledger explicitly marked
  ``NON_FINAL_STAGING``. Staging is deliberately separate from the canonical
  set and can never be consumed as one.
* **Resume-only-missing** -- a valid staged capture is never re-requested; a
  resume performs exactly one transport attempt per genuinely missing
  identity.
* **No retry/sleep loops** -- one authorized request is exactly one transport
  attempt. The first non-success response stops the invocation immediately.
  This module imports neither ``time`` nor any network library.
* **Canonical-consistency hash/conflict detection** -- a staged record whose
  raw body does not match its recorded canonical hash, whose request metadata
  disagrees with its deterministic identity, or which collides with an
  existing body under the same identity fails closed rather than being
  overwritten or trusted.
* **Atomic promote-only-when-complete** -- a canonical set is produced only
  from the exact complete expected identity set, built in a sibling temp
  directory and swapped in with renames; any failure leaves a previously
  promoted canonical set intact.
* **Raw evidence preserved even when invalid** -- every raw response body is
  retained. Successful bodies stay on disk in staging; non-success bodies are
  appended to the (never-rewritten-in-place) blocker history in the ledger.
  Nothing invalid is silently dropped or promoted.
* **Credentials never committed** -- the framework never reads, logs, stores,
  or writes a credential. It takes an injected ``transport`` and refuses
  request identities, metadata, or context whose keys look credential-like.

Usage sketch::

    spec = RequestSpec.build(
        endpoint="/some/endpoint",
        params={"ts_code": "000001.SZ", "start_date": "2020-01-01"},
        metadata={"role": "daily"},
    )
    capture = StagedCaptureSet(
        [spec],
        staging_dir=Path(".../staging"),
        ledger_path=Path(".../staging_ledger.json"),
        canonical_dir=Path(".../canonical"),
    )
    code = capture.run(transport)      # 0 complete, 3 blocked
    if code == 0:
        capture.promote(build_manifest=lambda valid: {...})
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

__all__ = [
    "DEFAULT_TRANSIENT_STATUSES",
    "RequestSpec",
    "STAGING_STATUS",
    "STATE_COMPLETE_NOT_PROMOTED",
    "STATE_INCOMPLETE",
    "StagedCaptureError",
    "StagedCaptureSet",
    "assert_credential_free",
    "canonical_body_json",
    "canonical_request_json",
    "content_sha256",
    "request_identity",
]

#: Ledger provenance marker. A ledger carrying anything else is refused.
STAGING_STATUS = "NON_FINAL_STAGING"

#: Ledger states. ``COMPLETE_NOT_PROMOTED`` still does not authorize
#: consumption: only the atomic promotion path produces a canonical set.
STATE_INCOMPLETE = "INCOMPLETE"
STATE_COMPLETE_NOT_PROMOTED = "COMPLETE_NOT_PROMOTED"

#: Transient operational blocks (never a permanent exclusion from the request
#: set). They are NEVER retried: one authorized request is one attempt, and
#: the first such response stops the run immediately.
DEFAULT_TRANSIENT_STATUSES = frozenset({429, 500, 502, 503, 504})

#: Substrings that mark a parameter/metadata/context key as credential-like.
#: The framework refuses to hash, stage, or ledger such a value.
_CREDENTIAL_KEY_MARKERS = (
    "api_key",
    "apikey",
    "token",
    "secret",
    "password",
    "passwd",
    "authorization",
    "credential",
    "access_key",
    "private_key",
)

#: A transport returns ``(status_code, body)`` for exactly one request. It
#: must not retry internally on the framework's behalf.
Transport = Callable[[str, Mapping[str, Any]], tuple[int, Any]]


class StagedCaptureError(RuntimeError):
    """A staged-capture invariant was violated; fail closed, never patch over."""


# ---------------------------------------------------------------------------
# Canonicalization / hashing / identity
# ---------------------------------------------------------------------------
def _canonical_json(value: object) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )


def canonical_body_json(body: object) -> str:
    """Canonical raw representation of a response body for hashing.

    Key order and insignificant whitespace never affect the hash, so a body
    recorded on one invocation and re-read on another is content-identical.
    """
    return _canonical_json(body)


def content_sha256(body: object) -> str:
    """Stable SHA-256 of a response body's canonical form."""
    return hashlib.sha256(canonical_body_json(body).encode("utf-8")).hexdigest()


def canonical_request_json(
    endpoint: str, params: Mapping[str, Any] | None = None
) -> str:
    """Canonical, order-independent representation of one request."""
    raw = dict(params or {})
    normalized = {str(key): raw[key] for key in sorted(raw, key=str)}
    return _canonical_json({"endpoint": endpoint, "params": normalized})


def _slug(text: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()
    return cleaned or "request"


def request_identity(
    endpoint: str, params: Mapping[str, Any] | None = None
) -> str:
    """Deterministic identity of one ``(endpoint, params)`` request.

    The identity is a pure function of the request: the same request always
    yields the same name, independent of caller order or time. The readable
    slug is cosmetic; the digest is what makes it collision-resistant.
    """
    digest = hashlib.sha256(
        canonical_request_json(endpoint, params).encode("utf-8")
    ).hexdigest()
    return f"{_slug(endpoint)}__{digest[:32]}"


def assert_credential_free(mapping: Mapping[str, Any] | None) -> None:
    """Refuse any mapping whose keys look like secrets.

    Credentials must never reach a staged record, the ledger, or a canonical
    artifact. The framework's transport owns authentication; request
    identities carry only non-secret addressing data.
    """
    if not mapping:
        return
    offending = [
        str(key)
        for key in mapping
        if any(marker in str(key).lower() for marker in _CREDENTIAL_KEY_MARKERS)
    ]
    if offending:
        raise StagedCaptureError(
            "credential-like key(s) "
            f"{sorted(offending)!r} are not allowed in a capture request, "
            "metadata, or context: credentials are never committed."
        )


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_json(path: Path, obj: object) -> None:
    path.write_text(
        json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# Request specification
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class RequestSpec:
    """One deterministic capture request.

    ``endpoint`` and ``params`` define the identity and are the only inputs
    sent to the transport. ``metadata`` and ``label`` are non-request
    provenance recorded alongside the capture; they never affect identity.
    ``canonical_name`` is the filename this capture promotes to (defaults to
    ``<identity>.json``).
    """

    endpoint: str
    params: Mapping[str, Any] = field(default_factory=dict)
    label: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)
    canonical_name: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "params", dict(self.params))
        object.__setattr__(self, "metadata", dict(self.metadata))
        if not self.endpoint:
            raise StagedCaptureError("endpoint must be non-empty")
        assert_credential_free(self.params)
        assert_credential_free(self.metadata)

    @classmethod
    def build(
        cls,
        endpoint: str,
        params: Mapping[str, Any] | None = None,
        *,
        label: str = "",
        metadata: Mapping[str, Any] | None = None,
        canonical_name: str | None = None,
    ) -> "RequestSpec":
        return cls(
            endpoint=endpoint,
            params=dict(params or {}),
            label=label,
            metadata=dict(metadata or {}),
            canonical_name=canonical_name,
        )

    @property
    def identity(self) -> str:
        return request_identity(self.endpoint, self.params)

    @property
    def resolved_canonical_name(self) -> str:
        return self.canonical_name or f"{self.identity}.json"

    def to_meta(self) -> dict[str, Any]:
        """Ledger-visible description of this request (no secrets by design)."""
        return {
            "identity": self.identity,
            "endpoint": self.endpoint,
            "params": dict(self.params),
            "label": self.label,
            "metadata": dict(self.metadata),
            "canonical_name": self.resolved_canonical_name,
        }


# ---------------------------------------------------------------------------
# Staged capture set
# ---------------------------------------------------------------------------
class StagedCaptureSet:
    """A resumable, bounded capture of one fixed request-identity set.

    The set is fixed at construction. Adding, dropping, or renaming a request
    means constructing a new set with its own staging area/ledger; a ledger
    that describes a different identity set is refused rather than merged.
    """

    def __init__(
        self,
        specs: list[RequestSpec] | tuple[RequestSpec, ...],
        *,
        staging_dir: str | Path,
        ledger_path: str | Path,
        canonical_dir: str | Path | None = None,
        transient_statuses: frozenset[int] = DEFAULT_TRANSIENT_STATUSES,
        context: Mapping[str, Any] | None = None,
        promotion_guard: Callable[[dict[str, dict]], None] | None = None,
        now: Callable[[], str] | None = None,
    ) -> None:
        self._specs: tuple[RequestSpec, ...] = tuple(specs)
        if not self._specs:
            raise StagedCaptureError("a capture set must contain >= 1 request")
        self._staging_dir = Path(staging_dir)
        self._ledger_path = Path(ledger_path)
        self._canonical_dir = (
            Path(canonical_dir) if canonical_dir is not None else None
        )
        self._transient = frozenset(transient_statuses)
        assert_credential_free(context)
        self._context = dict(context or {})
        self._promotion_guard = promotion_guard
        self._now = now or _utc_now

        expected: dict[str, RequestSpec] = {}
        for spec in self._specs:
            if spec.identity in expected:
                raise StagedCaptureError(
                    "duplicate request identity "
                    f"{spec.identity!r}: two specs resolve to the same "
                    "request; the identity set must be unique."
                )
            expected[spec.identity] = spec
        canonical_names = [
            spec.resolved_canonical_name for spec in self._specs
        ]
        if len(set(canonical_names)) != len(canonical_names):
            raise StagedCaptureError(
                "duplicate canonical filename in capture set"
            )
        self._expected = expected

    # -- introspection -----------------------------------------------------
    @property
    def specs(self) -> tuple[RequestSpec, ...]:
        return self._specs

    @property
    def staging_dir(self) -> Path:
        return self._staging_dir

    @property
    def ledger_path(self) -> Path:
        return self._ledger_path

    @property
    def canonical_dir(self) -> Path | None:
        return self._canonical_dir

    def identity_set(self) -> list[str]:
        return [spec.identity for spec in self._specs]

    def staged_path(self, identity: str) -> Path:
        return self._staging_dir / f"{identity}.json"

    def is_complete(self) -> bool:
        return set(self.load_valid()) == set(self._expected)

    # -- staged-record integrity ------------------------------------------
    def _validate_record(
        self, record: dict, spec: RequestSpec, path: Path
    ) -> None:
        """Fail closed on any identity/metadata/hash inconsistency."""
        if not isinstance(record, dict):
            raise StagedCaptureError(
                f"staged capture {path.name} is not a JSON object."
            )
        if record.get("identity") != spec.identity:
            raise StagedCaptureError(
                f"staged capture {path.name} identity mismatch "
                f"({record.get('identity')!r} != {spec.identity!r})."
            )
        if record.get("endpoint") != spec.endpoint:
            raise StagedCaptureError(
                f"staged capture {path.name} endpoint mismatch."
            )
        if dict(record.get("params", {})) != dict(spec.params):
            raise StagedCaptureError(
                f"staged capture {path.name} request-parameter mismatch."
            )
        if record.get("label", "") != spec.label:
            raise StagedCaptureError(
                f"staged capture {path.name} label mismatch."
            )
        if dict(record.get("metadata", {})) != dict(spec.metadata):
            raise StagedCaptureError(
                f"staged capture {path.name} metadata conflict."
            )
        if not record.get("retrieved_at_utc"):
            raise StagedCaptureError(
                f"staged capture {path.name} lacks a retrieval timestamp."
            )
        if int(record.get("status_code", 0)) != 200:
            raise StagedCaptureError(
                f"staged capture {path.name} is not an HTTP 200 success."
            )
        actual = content_sha256(record.get("body"))
        if actual != record.get("content_sha256"):
            raise StagedCaptureError(
                f"staged capture {path.name} content hash mismatch "
                f"({actual} != {record.get('content_sha256')}); failing closed."
            )

    def load_valid(self) -> dict[str, dict]:
        """Validate staged captures already on disk; return ``{identity: rec}``.

        Any unexpected staged file, metadata conflict, or hash mismatch fails
        closed. No transport is called here.
        """
        valid: dict[str, dict] = {}
        if not self._staging_dir.is_dir():
            return valid
        for path in sorted(self._staging_dir.iterdir()):
            if not path.is_file():
                continue
            if not path.name.endswith(".json"):
                raise StagedCaptureError(
                    f"unexpected staged file {path.name!r}; failing closed."
                )
            identity = path.name[: -len(".json")]
            if identity not in self._expected:
                raise StagedCaptureError(
                    f"unexpected staged capture {path.name!r}; failing closed."
                )
            record = json.loads(path.read_text(encoding="utf-8"))
            self._validate_record(record, self._expected[identity], path)
            valid[identity] = record
        return valid

    def _write_staged(
        self, spec: RequestSpec, body: Any, *, retrieved_at: str | None = None
    ) -> dict:
        """Persist exactly one successful response into the non-final area."""
        self._staging_dir.mkdir(parents=True, exist_ok=True)
        record = {
            "identity": spec.identity,
            "endpoint": spec.endpoint,
            "params": dict(spec.params),
            "label": spec.label,
            "metadata": dict(spec.metadata),
            "retrieved_at_utc": retrieved_at or self._now(),
            "status_code": 200,
            "body_format": "json-canonical",
            "content_sha256": content_sha256(body),
            "body": body,
        }
        _write_json(self.staged_path(spec.identity), record)
        return record

    def ingest_body(
        self,
        spec: RequestSpec,
        body: Any,
        *,
        retrieved_at: str | None = None,
    ) -> dict:
        """Stage a caller-provided body without any network access.

        Canonical-consistency guard: if a valid record already exists for this
        identity, the new body's canonical hash must match it. A mismatch is a
        conflict and fails closed rather than silently overwriting evidence.
        """
        if spec.identity not in self._expected:
            raise StagedCaptureError(
                f"{spec.identity!r} is not part of this capture set."
            )
        existing = self.load_valid().get(spec.identity)
        new_hash = content_sha256(body)
        if existing is not None:
            if existing["content_sha256"] != new_hash:
                raise StagedCaptureError(
                    f"canonical conflict for {spec.identity!r}: staged content "
                    f"{existing['content_sha256']} != incoming {new_hash}; "
                    "refusing to overwrite."
                )
            return existing
        return self._write_staged(
            spec, body, retrieved_at=retrieved_at
        )

    # -- ledger ------------------------------------------------------------
    def _load_prior_blockers(self) -> list[dict]:
        """Read the append-only blocker history from an existing ledger.

        Only a ``NON_FINAL_STAGING`` ledger describing this exact identity set
        is consumable; anything else fails closed.
        """
        if not self._ledger_path.is_file():
            return []
        ledger = json.loads(self._ledger_path.read_text(encoding="utf-8"))
        provenance = ledger.get("_provenance", {})
        if provenance.get("status") != STAGING_STATUS:
            raise StagedCaptureError(
                f"{self._ledger_path} is not a {STAGING_STATUS} ledger; "
                "refusing to consume it."
            )
        recorded = [
            entry.get("identity")
            for entry in ledger.get("expected_identities", [])
        ]
        if recorded != self.identity_set():
            raise StagedCaptureError(
                "staging ledger expected-identity set does not match this "
                "capture set; refusing to consume it."
            )
        blockers = ledger.get("blockers", [])
        if not isinstance(blockers, list):
            raise StagedCaptureError("malformed blockers history in ledger.")
        return list(blockers)

    def _write_ledger(
        self,
        valid: dict[str, dict],
        *,
        state: str,
        blocker: dict | None,
        blockers: list[dict],
    ) -> None:
        """Write the explicitly non-final staging ledger (never canonical)."""
        ledger = {
            "_provenance": {
                "status": STAGING_STATUS,
                "note": (
                    "Non-final staged capture state. NOT certification "
                    "evidence and NOT a canonical set; it may only be promoted "
                    "through the validated atomic promotion path."
                ),
                "context": dict(self._context),
                "expected_identity_count": len(self._specs),
                "valid_captured_count": len(valid),
                "state": state,
                "updated_at_utc": self._now(),
            },
            "expected_identities": [spec.to_meta() for spec in self._specs],
            "valid_captured_identities": [
                spec.identity
                for spec in self._specs
                if spec.identity in valid
            ],
            "remaining_identities": [
                spec.identity
                for spec in self._specs
                if spec.identity not in valid
            ],
            "captures": {
                spec.identity: {
                    "endpoint": valid[spec.identity]["endpoint"],
                    "params": dict(valid[spec.identity]["params"]),
                    "retrieved_at_utc": valid[spec.identity][
                        "retrieved_at_utc"
                    ],
                    "status_code": valid[spec.identity]["status_code"],
                    "content_sha256": valid[spec.identity]["content_sha256"],
                }
                for spec in self._specs
                if spec.identity in valid
            },
            "blockers": blockers,
            "last_blocker": blocker,
        }
        self._ledger_path.parent.mkdir(parents=True, exist_ok=True)
        _write_json(self._ledger_path, ledger)

    # -- capture -----------------------------------------------------------
    @staticmethod
    def _unpack(result: object) -> tuple[int, Any]:
        if not isinstance(result, tuple) or len(result) != 2:
            raise StagedCaptureError(
                "transport must return (status_code, body)"
            )
        status, body = result
        return int(status), body

    def _fetch_once(self, transport: Transport, spec: RequestSpec) -> tuple[
        int, Any, str | None
    ]:
        """Exactly ONE transport attempt for one request. No retry/sleep."""
        try:
            result = transport(spec.endpoint, dict(spec.params))
        except Exception as exc:  # noqa: BLE001 - any failure is a blocker
            return 0, None, repr(exc)
        status, body = self._unpack(result)
        return status, body, None

    def run(self, transport: Transport) -> int:
        """Resume + capture only genuinely missing identities.

        Returns ``0`` when the complete expected set is staged (state
        ``COMPLETE_NOT_PROMOTED``) and ``3`` when a response stopped the run
        (state ``INCOMPLETE``). One authorized request is exactly one
        transport attempt; prior valid staged successes and the prior blocker
        history survive. No canonical file is written here.
        """
        valid = self.load_valid()
        blockers = self._load_prior_blockers()
        self._write_ledger(
            valid, state=STATE_INCOMPLETE, blocker=None, blockers=blockers
        )
        for spec in self._specs:
            identity = spec.identity
            if identity in valid:
                continue
            status, body, error = self._fetch_once(transport, spec)
            if status == 200:
                valid[identity] = self._write_staged(spec, body)
                self._write_ledger(
                    valid,
                    state=STATE_INCOMPLETE,
                    blocker=None,
                    blockers=blockers,
                )
                continue
            classification = (
                "transient" if status in self._transient else "unexpected"
            )
            blocker = {
                "identity": identity,
                "endpoint": spec.endpoint,
                "params": dict(spec.params),
                "status_code": status,
                "classification": classification,
                "error": error,
                "body": body,
                "recorded_at_utc": self._now(),
            }
            blockers.append(blocker)
            self._write_ledger(
                valid,
                state=STATE_INCOMPLETE,
                blocker=blocker,
                blockers=blockers,
            )
            return 3
        self._write_ledger(
            valid,
            state=STATE_COMPLETE_NOT_PROMOTED,
            blocker=None,
            blockers=blockers,
        )
        return 0

    # -- promotion ---------------------------------------------------------
    def _validate_complete(self, valid: dict[str, dict]) -> None:
        expected = set(self._expected)
        if set(valid) != expected:
            missing = sorted(expected - set(valid))
            unexpected = sorted(set(valid) - expected)
            raise StagedCaptureError(
                "staged set is not the exact expected identity set; "
                f"missing={missing}, unexpected={unexpected}."
            )
        for spec in self._specs:
            self._validate_record(
                valid[spec.identity],
                spec,
                self.staged_path(spec.identity),
            )

    def promote(
        self,
        *,
        canonical_dir: str | Path | None = None,
        build_manifest: Callable[[dict[str, dict]], object] | None = None,
    ) -> None:
        """Atomically promote a complete validated staged set to canonical.

        Consumers only ever see the previous valid set or the new complete
        set: bodies (and an optional ``manifest.json``) are built in a sibling
        temp directory and swapped in with renames. Any failure leaves the
        previous canonical set intact.
        """
        target = (
            Path(canonical_dir)
            if canonical_dir is not None
            else self._canonical_dir
        )
        if target is None:
            raise StagedCaptureError(
                "no canonical_dir configured for promotion."
            )
        valid = self.load_valid()
        self._validate_complete(valid)
        if self._promotion_guard is not None:
            self._promotion_guard(valid)

        parent = target.parent
        parent.mkdir(parents=True, exist_ok=True)
        tmp_dir = parent / (target.name + ".promote-tmp")
        backup_dir = parent / (target.name + ".promote-backup")
        shutil.rmtree(tmp_dir, ignore_errors=True)
        shutil.rmtree(backup_dir, ignore_errors=True)
        tmp_dir.mkdir(parents=True)
        try:
            for spec in self._specs:
                _write_json(
                    tmp_dir / spec.resolved_canonical_name,
                    valid[spec.identity]["body"],
                )
            if build_manifest is not None:
                _write_json(
                    tmp_dir / "manifest.json", build_manifest(valid)
                )
        except BaseException:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            raise

        had_previous = target.exists()
        if had_previous:
            os.rename(target, backup_dir)
        try:
            os.rename(tmp_dir, target)
        except BaseException:
            if had_previous and backup_dir.exists():
                os.rename(backup_dir, target)
            shutil.rmtree(tmp_dir, ignore_errors=True)
            raise
        shutil.rmtree(backup_dir, ignore_errors=True)


def main() -> int:
    """No CLI by design: this is a library used by a task-owned recorder.

    A capture needs a domain-specific transport, request-identity set, and
    universe guard, which belong to the calling script (e.g. the Phase 5B
    China pilot), not to this generic module.
    """
    raise SystemExit(
        "scripts/staged_capture.py is a library module; import StagedCaptureSet "
        "from a task-owned recorder instead of running it directly."
    )


if __name__ == "__main__":
    raise SystemExit(main())
