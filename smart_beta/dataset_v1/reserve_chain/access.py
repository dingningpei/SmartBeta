"""Campaign-3 P7 task R7-X — the K-PIT ``ACCESS`` gate for the reserve chain.

Authority: ``docs/campaign3_p7_implementation_spec.md`` §4 and
``docs/campaign3_p7_reserve_path_design.md`` §2 (principle 1, "one access") /
§3.  The design says the real reserve derivation runs **once**, inside an
authorized confirmation execution, *preceded* by a K-PIT ``ACCESS`` record
naming the hypothesis, the admitted procedure and the exact code hashes.

This module owns the gate:

* :func:`reserve_chain_module_sha256s` — the exact source SHA-256s of the
  ``smart_beta/dataset_v1/reserve_chain`` package (the "exact reserve_chain
  module source SHA-256s" of §4);
* :class:`AccessGrant` — the R7-U stub's concrete subclass, extended with
  :meth:`AccessGrant.from_kpit`, which reads one K-PIT ``ACCESS`` record and
  fails closed on *any* mismatch (hypothesis identity, procedure identity,
  module source hashes, fold window);
* the single-use ledger: :data:`CONSUMPTION_COMPONENT`.  A consumed grant is a
  second K-PIT ``ACCESS`` record for the same hypothesis; K-PIT is the ledger,
  so a second reserve derivation for a consumed hypothesis is refused.

No production K-PIT log is ever read or written here.  The gate takes an
injected K-PIT log (tests use a disposable one).  This module opens no data
path at all: it only hashes its own package's source files.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from smart_beta.dataset_v1 import universe as _universe
from smart_beta.dataset_v1.reserve_chain.fold import (
    AccessGrant as _StubAccessGrant,
    ReserveAccessDenied,
)
from smart_beta.science.contracts import RecordKind

__all__ = [
    "ADMITTED_PROCEDURE_ID",
    "ADMITTED_PROCEDURE_VERSION",
    "ADMITTED_PROCEDURE_CONTRACT_HASH",
    "ACCESS_COMPONENT",
    "CONSUMPTION_COMPONENT",
    "AccessGrant",
    "reserve_chain_module_sha256s",
    "build_access_payload",
    "build_consumption_payload",
    "consumption_record_hash",
]

#: The one procedure P7 may confirm with (implementation spec §3/§4).
ADMITTED_PROCEDURE_ID = "EWC_T23_ONE_SIDED"
ADMITTED_PROCEDURE_VERSION = "1.0.0"

#: ``EWCT23OneSided.contract.contract_hash`` (PA admission
#: ``ac14e033ec31e6428bdd815011f456daf998b4003be825bb418485c22ff5dd9c``).
ADMITTED_PROCEDURE_CONTRACT_HASH = (
    "ac14e033ec31e6428bdd815011f456daf998b4003be825bb418485c22ff5dd9c"
)

#: The authorizing ``ACCESS`` record's component marker.
ACCESS_COMPONENT = "reserve_chain:access"

#: The single-use ledger marker: a second ``ACCESS`` record with this component
#: and the ``hypothesis_id`` payload means the grant has been consumed.
CONSUMPTION_COMPONENT = "reserve_chain:consumed"

#: The reserve fold always starts at the sealed boundary (design §2).
_RESERVE_START = _universe.RESERVE_START

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# ---------------------------------------------------------------------------
# small, fail-closed validators
# ---------------------------------------------------------------------------


def _require_non_empty_str(value: Any, *, where: str) -> str:
    if not isinstance(value, str) or not value:
        raise ReserveAccessDenied(
            f"{where} must be a non-empty string, got {value!r}"
        )
    return value


def _require_sha256(value: Any, *, where: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.match(value) is None:
        raise ReserveAccessDenied(
            f"{where} must be a lowercase 64-hex sha256, got {value!r}"
        )
    return value


def _require_date(value: Any, *, where: str) -> date:
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or _ISO_DATE_RE.match(value) is None:
        raise ReserveAccessDenied(
            f"{where} must be an ISO YYYY-MM-DD date, got {value!r}"
        )
    try:
        return date.fromisoformat(value)
    except ValueError as exc:  # pragma: no cover - defensive
        raise ReserveAccessDenied(f"{where} is not a valid date: {exc}") from exc


def _module_map(value: Any) -> dict[str, str]:
    """Normalize the ``module_sha256`` payload to ``{name: sha256}``."""
    if isinstance(value, Mapping):
        items = value.items()
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        pairs: list[tuple[Any, Any]] = []
        for pair in value:
            if not isinstance(pair, Sequence) or len(pair) != 2:
                raise ReserveAccessDenied(
                    "module_sha256 entries must be (name, sha256) pairs"
                )
            pairs.append((pair[0], pair[1]))
        items = pairs
    else:
        raise ReserveAccessDenied(
            "ACCESS module_sha256 must be a mapping or a sequence of pairs"
        )
    normalized: dict[str, str] = {}
    for name, digest in items:
        key = _require_non_empty_str(name, where="module_sha256 name")
        normalized[key] = _require_sha256(
            digest, where=f"module_sha256[{key!r}]"
        )
    if not normalized:
        raise ReserveAccessDenied("ACCESS module_sha256 must name at least one module")
    return normalized


# ---------------------------------------------------------------------------
# K-PIT record access (a KnowledgeLog or a verified record sequence)
# ---------------------------------------------------------------------------


def _read_records(log: Any) -> tuple[Any, ...]:
    reader = getattr(log, "read", None)
    if callable(reader):
        return tuple(reader())
    if isinstance(log, (str, bytes)) or not isinstance(log, Sequence):
        raise ReserveAccessDenied(
            "the K-PIT ledger must be a KnowledgeLog or a record sequence"
        )
    return tuple(log)


def _record_hash(record: Any) -> str:
    if hasattr(record, "record_hash"):
        return str(record.record_hash)
    return str(record["record_hash"])


def _record_kind(record: Any) -> str:
    kind = record.kind if hasattr(record, "kind") else record["kind"]
    return kind.value if hasattr(kind, "value") else str(kind)


def _record_payload(record: Any) -> Mapping[str, Any]:
    payload = record.payload if hasattr(record, "payload") else record["payload"]
    return payload


# ---------------------------------------------------------------------------
# module source hashes
# ---------------------------------------------------------------------------


def _package_dir() -> Path:
    return Path(__file__).resolve().parent


def reserve_chain_module_sha256s(
    package_dir: Path | str | None = None,
) -> tuple[tuple[str, str], ...]:
    """The exact source SHA-256 of every ``reserve_chain`` module.

    Sorted by module file name so the tuple is deterministic.  This is the
    "exact reserve_chain module source SHA-256s" named by the ``ACCESS``
    record (implementation spec §4).
    """
    directory = Path(package_dir) if package_dir is not None else _package_dir()
    entries: list[tuple[str, str]] = []
    for path in sorted(directory.glob("*.py"), key=lambda item: item.name):
        entries.append(
            (path.name, hashlib.sha256(path.read_bytes()).hexdigest())
        )
    if not entries:
        raise ReserveAccessDenied(
            f"no reserve_chain module source found under {directory}"
        )
    return tuple(entries)


# ---------------------------------------------------------------------------
# the single-use ledger
# ---------------------------------------------------------------------------


def consumption_record_hash(
    records: Sequence[Any], hypothesis_id: str
) -> str | None:
    """The K-PIT hash of the consumption marker for ``hypothesis_id``, if any."""
    for record in records:
        if _record_kind(record) != RecordKind.ACCESS.value:
            continue
        payload = _record_payload(record)
        if (
            payload.get("component") == CONSUMPTION_COMPONENT
            and payload.get("hypothesis_id") == hypothesis_id
        ):
            return _record_hash(record)
    return None


# ---------------------------------------------------------------------------
# payload builders (used by P6 and by the disposable test logs)
# ---------------------------------------------------------------------------


def build_access_payload(
    *,
    artifact_record_hash: str,
    hypothesis_id: str,
    fold_start: date,
    fold_end: date,
    module_sha256s: Mapping[str, str] | Sequence[tuple[str, str]] | None = None,
    procedure_id: str = ADMITTED_PROCEDURE_ID,
    procedure_version: str = ADMITTED_PROCEDURE_VERSION,
    procedure_contract_hash: str = ADMITTED_PROCEDURE_CONTRACT_HASH,
) -> dict[str, Any]:
    """Build the canonical ``ACCESS`` payload the gate accepts."""
    modules = (
        dict(module_sha256s)
        if module_sha256s is not None
        else dict(reserve_chain_module_sha256s())
    )
    return {
        "artifact_record_hash": _require_sha256(
            artifact_record_hash, where="artifact_record_hash"
        ),
        "component": ACCESS_COMPONENT,
        "hypothesis_id": _require_non_empty_str(
            hypothesis_id, where="hypothesis_id"
        ),
        "procedure_id": _require_non_empty_str(
            procedure_id, where="procedure_id"
        ),
        "procedure_version": _require_non_empty_str(
            procedure_version, where="procedure_version"
        ),
        "procedure_contract_hash": _require_sha256(
            procedure_contract_hash, where="procedure_contract_hash"
        ),
        "module_sha256": dict(modules),
        "fold": {
            "start": fold_start.isoformat(),
            "end": fold_end.isoformat(),
        },
    }


def build_consumption_payload(
    *,
    artifact_record_hash: str,
    hypothesis_id: str,
) -> dict[str, Any]:
    """Build the single-use ledger record payload for one hypothesis."""
    return {
        "artifact_record_hash": _require_sha256(
            artifact_record_hash, where="artifact_record_hash"
        ),
        "component": CONSUMPTION_COMPONENT,
        "hypothesis_id": _require_non_empty_str(
            hypothesis_id, where="hypothesis_id"
        ),
    }


# ---------------------------------------------------------------------------
# the grant
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AccessGrant(_StubAccessGrant):
    """The K-PIT ``ACCESS`` grant (R7-X), a :class:`fold.AccessGrant`.

    The R7-U stub carries the hypothesis, procedure id, fold window and module
    hashes.  R7-X adds the admitted procedure's version/contract hash, the
    authorizing K-PIT record hash and the artifact the record is bound to so
    the executor can append the single-use marker.
    """

    procedure_version: str = ""
    procedure_contract_hash: str = ""
    access_record_hash: str = ""
    artifact_record_hash: str = ""

    @classmethod
    def from_kpit(
        cls,
        log: Any,
        record_hash: str,
        *,
        current_module_sha256s: Mapping[str, str] | Sequence[tuple[str, str]]
        | None = None,
    ) -> "AccessGrant":
        """Read one K-PIT ``ACCESS`` record and return the verified grant.

        Every condition is a hard gate (design §2 principle 1):

        * the named record must exist, be an ``ACCESS`` record and not itself be
          a consumption marker;
        * the payload must name a hypothesis, the admitted procedure
          (:data:`ADMITTED_PROCEDURE_ID` / :data:`ADMITTED_PROCEDURE_VERSION` /
          :data:`ADMITTED_PROCEDURE_CONTRACT_HASH`) and a reserve fold window;
        * the payload's ``module_sha256`` must equal the current
          ``reserve_chain`` source hashes exactly;
        * the grant must not already be consumed.

        Any mismatch raises :class:`ReserveAccessDenied`.
        """
        if not isinstance(record_hash, str) or not record_hash:
            raise ReserveAccessDenied("record_hash must be a non-empty string")
        records = _read_records(log)
        record = next(
            (item for item in records if _record_hash(item) == record_hash), None
        )
        if record is None:
            raise ReserveAccessDenied(
                f"no K-PIT record named {record_hash!r}"
            )
        if _record_kind(record) != RecordKind.ACCESS.value:
            raise ReserveAccessDenied(
                "the access record is not a K-PIT ACCESS record"
            )
        payload = _record_payload(record)
        if payload.get("component") == CONSUMPTION_COMPONENT:
            raise ReserveAccessDenied(
                "a consumption marker is not an authorization grant"
            )

        hypothesis_id = _require_non_empty_str(
            payload.get("hypothesis_id"), where="ACCESS hypothesis_id"
        )
        procedure_id = _require_non_empty_str(
            payload.get("procedure_id"), where="ACCESS procedure_id"
        )
        procedure_version = _require_non_empty_str(
            payload.get("procedure_version"), where="ACCESS procedure_version"
        )
        procedure_contract_hash = _require_sha256(
            payload.get("procedure_contract_hash"),
            where="ACCESS procedure_contract_hash",
        )
        if (
            procedure_id != ADMITTED_PROCEDURE_ID
            or procedure_version != ADMITTED_PROCEDURE_VERSION
            or procedure_contract_hash != ADMITTED_PROCEDURE_CONTRACT_HASH
        ):
            raise ReserveAccessDenied(
                "the access record names a procedure that is not the admitted "
                f"{ADMITTED_PROCEDURE_ID} {ADMITTED_PROCEDURE_VERSION} "
                f"({ADMITTED_PROCEDURE_CONTRACT_HASH})"
            )

        declared_modules = _module_map(payload.get("module_sha256"))
        current_modules = dict(
            current_module_sha256s
            if current_module_sha256s is not None
            else reserve_chain_module_sha256s()
        )
        if declared_modules != current_modules:
            missing = sorted(set(current_modules) - set(declared_modules))
            extra = sorted(set(declared_modules) - set(current_modules))
            changed = sorted(
                name
                for name in set(declared_modules) & set(current_modules)
                if declared_modules[name] != current_modules[name]
            )
            raise ReserveAccessDenied(
                "the recorded reserve_chain module source hashes do not match "
                f"the loaded code (missing={missing}, extra={extra}, "
                f"changed={changed})"
            )

        raw_fold = payload.get("fold")
        if not isinstance(raw_fold, Mapping):
            raise ReserveAccessDenied("ACCESS fold must be a mapping")
        fold_start = _require_date(raw_fold.get("start"), where="ACCESS fold.start")
        fold_end = _require_date(raw_fold.get("end"), where="ACCESS fold.end")
        if fold_start != _RESERVE_START:
            raise ReserveAccessDenied(
                f"ACCESS fold.start must be the reserve boundary "
                f"{_RESERVE_START.isoformat()}, got {fold_start.isoformat()}"
            )
        if fold_end < fold_start:
            raise ReserveAccessDenied(
                f"ACCESS fold end {fold_end.isoformat()} precedes its start"
            )

        if consumption_record_hash(records, hypothesis_id) is not None:
            raise ReserveAccessDenied(
                f"the reserve grant for hypothesis {hypothesis_id!r} is already "
                "consumed (K-PIT is the ledger)"
            )

        return cls(
            hypothesis_id=hypothesis_id,
            procedure_id=procedure_id,
            fold_start=fold_start,
            fold_end=fold_end,
            module_sha256=tuple(sorted(declared_modules.items())),
            procedure_version=procedure_version,
            procedure_contract_hash=procedure_contract_hash,
            access_record_hash=record_hash,
            artifact_record_hash=_require_sha256(
                payload.get("artifact_record_hash"),
                where="ACCESS artifact_record_hash",
            ),
        )
