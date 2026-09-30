"""Discovery Dataset v1.1 — D7c development → Phase-6 ``TrustedInput`` bridge.

D7c turns the frozen D3 (development observations / corporate actions), D4
(identity intervals), D5 (QC day flags) and D-SC (class / policy timelines)
artifacts into the three contract-§7 inputs -- ``daily_total_return``,
``total_return_index`` and ``dollar_volume`` -- and delivers them to Phase 6
as :class:`~smart_beta.spec.engine.TrustedInput` values under the §11.1
one-trusted-session lag.

Governing authority (frozen), in precedence order: ``docs/
discovery_dataset_v1_contract.md`` §§3, 5, 7, 8, 10, 11.1, 16;
``docs/discovery_dataset_v1_1_d7c_clarification.md``; ``docs/
discovery_dataset_v1_1_d7_clarification.md``; ``docs/
discovery_dataset_v1_1_d6_clarification.md``; ``docs/
discovery_dataset_v1_1_classification_amendment.md``; ``docs/
discovery_dataset_v1_1_identity_binding_clarification.md``; and
``worker_tasks/dataset_v1/task-d6-universe.md`` §5.

Reserve firewall (mechanical, fail closed): the D3 development files are read
**only** through the unmodified :class:`~smart_beta.dataset_v1.reserve_firewall.
DevelopmentAccessor`; the D4/D5/D-SC files are opened **only** through one
guarded :class:`Resolver` whose allowlist is derived from the frozen
:class:`Bindings`.  The resolver refuses any path whose components include
``reserve_sealed``, ``raw`` or ``dsc_evidence``, and any path not in the
allowlist.  After loading, every loaded observation, day-flag, corporate-action
and D-SC evidence date must be earlier than 2023-07-03.

The builder reads the raw sources; the loader reads only the emitted D7c
artifact under ``<root>/derived/trusted_inputs/<ti_id>/`` and never the raw
sources.  The module never makes a provider or network call, never reads a
credential value, never reads the real ``data_store`` (tests inject synthetic
bindings and ``tmp_path`` fixtures), never reads a vendor ``adj*`` field, and
never re-derives a D6/D-SC decision.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import tempfile
from dataclasses import dataclass, replace
from datetime import date, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd
import pyarrow as pa

from smart_beta.config.settings import DEFAULT_SETTINGS
from smart_beta.dataset_v1.classification import (
    class_state_at,
    load_class_timeline,
    load_policy_timeline,
    policy_state_at,
)
from smart_beta.dataset_v1.reserve_firewall import (
    DevelopmentAccessor,
    ReserveFirewallError,
    FileBinding as AccessorFileBinding,
)
from smart_beta.pit.corporate_actions import compute_adjusted_returns
from smart_beta.spec.engine import EvidenceClass, TrustedInput
from smart_beta.spec.requirements import (
    DataCapability,
    Frequency,
    ObservationPeriod,
    RevisionPolicy,
    Unit,
)
from smart_beta.vendors.tiingo.calendar_source import build_nyse_calendar

__all__ = [
    "SCHEMA",
    "RESERVE_START",
    "DEVELOPMENT_END",
    "LAST_SOURCE_DATE",
    "CALENDAR_START",
    "CALENDAR_END",
    "AS_OF",
    "PIT_BUFFER_DAYS",
    "INPUT_NAMES",
    "INPUT_DAILY_TOTAL_RETURN",
    "INPUT_TOTAL_RETURN_INDEX",
    "INPUT_DOLLAR_VOLUME",
    "CLASS_COMMON_STOCK",
    "POLICY_NO_EXCLUSION",
    "STATE_INTERVAL_ASSIGNED",
    "FileBinding",
    "StageBinding",
    "HashBinding",
    "Bindings",
    "Resolver",
    "D7cError",
    "D7cInputError",
    "D7cBindingError",
    "D7cConsistencyError",
    "NondeterministicTrustedInputsError",
    "canonical_json",
    "default_bindings",
    "build_trusted_inputs",
    "load_trusted_inputs",
    "main",
]


# ---------------------------------------------------------------------------
# Frozen constants
# ---------------------------------------------------------------------------

SCHEMA = "discovery-dataset-v1-trusted-inputs-v1"

#: Fixed start of the sealed historical reserve (contract §§11.2, 12).
RESERVE_START = date(2023, 7, 3)
#: Last exposed development date / last grid row.
DEVELOPMENT_END = date(2023, 6, 30)
#: Last development *source* date a primitive may be dated (prev of the grid end).
LAST_SOURCE_DATE = date(2023, 6, 29)
#: Trusted-calendar bounds (task spec §3).
CALENDAR_START = date(2015, 1, 2)
CALENDAR_END = date(2027, 12, 31)
#: The corporate-action knowledge cutoff and buffer (task spec §4).
AS_OF = date(2023, 6, 30)
PIT_BUFFER_DAYS = 0

INPUT_DAILY_TOTAL_RETURN = "daily_total_return"
INPUT_TOTAL_RETURN_INDEX = "total_return_index"
INPUT_DOLLAR_VOLUME = "dollar_volume"
INPUT_NAMES = (
    INPUT_DAILY_TOTAL_RETURN,
    INPUT_TOTAL_RETURN_INDEX,
    INPUT_DOLLAR_VOLUME,
)

CLASS_COMMON_STOCK = "COMMON_STOCK"
POLICY_NO_EXCLUSION = "NO_REGULATORY_EXCLUSION_OBSERVED"
POLICY_EXCLUDED = "EXCLUDED"
POLICY_ISSUER_STATUS_UNRESOLVED = "ISSUER_STATUS_UNRESOLVED"
CLASS_UNRESOLVED = "CLASS_UNRESOLVED"

STATE_INTERVAL_ASSIGNED = "INTERVAL_ASSIGNED"

DISPOSITION_CORRUPT = "CORRUPT"
CLASS_INTERVAL_MISMATCH = "INTERVAL_MISMATCH"
CLASS_VENDOR_NULL = "VENDOR_NULL"
CLASS_CORP_ACTION_SUSPECT = "CORP_ACTION_SUSPECT"
CORRUPT_FLAGS = frozenset(
    {
        "OHLC_INCONSISTENT",
        "NONPOSITIVE_PRICE",
        "MALFORMED_CA",
        "DUPLICATE_DATE",
        "OFF_CALENDAR_ROW",
    }
)

#: The recorded knowledge-date rule (task spec §6, clarification §6).
KNOWLEDGE_DATE_RULE = (
    "row d holds the end-of-day observation of prev(d); values as served at C "
    "(vendor revision history not certified)"
)

CERTIFICATION_BOUNDARY = (
    "D7c certifies the deterministic, development-only construction and "
    "Phase-6 delivery of the contract-required daily_total_return (the "
    "contract-§8 total return), total_return_index (segment-1-only, exact "
    "within one uninterrupted valid-return segment and fail-closed across any "
    "break for every legal Phase-6 lookback) and dollar_volume (raw close x "
    "raw volume) TrustedInputs, each masked to the known-at-the-time D-SC "
    "common-stock cross-section, using the D4 identity intervals, under the "
    "frozen one-trusted-session lag, with gate-13 timing and future-data "
    "invariance demonstrated. Not claimed: factor efficacy, alpha or "
    "predictive validity; any Campaign-1 result; confirmation eligibility or "
    "reserve evidence; identity continuity beyond D4 or issuer-identity "
    "timing; delisting proceeds; survivorship completeness; volume semantics; "
    "vendor revision history; production readiness."
)

_STRING = pd.ArrowDtype(pa.string())
_DATE32 = pd.ArrowDtype(pa.date32())
_INT64 = pd.ArrowDtype(pa.int64())
_BOOL = pd.ArrowDtype(pa.bool_())
_FLOAT64 = pd.ArrowDtype(pa.float64())

DAILY_FILE = "daily_total_return.parquet"
TRI_FILE = "total_return_index.parquet"
DV_FILE = "dollar_volume.parquet"
SEGMENTS_FILE = "segments.parquet"
MANIFEST_FILE = "trusted_inputs.json"

_INPUT_FILES = {
    INPUT_DAILY_TOTAL_RETURN: DAILY_FILE,
    INPUT_TOTAL_RETURN_INDEX: TRI_FILE,
    INPUT_DOLLAR_VOLUME: DV_FILE,
}
_LONG_COLUMNS = ("row_date", "security_key", "source_session", "value")
_LONG_DTYPES: dict[str, Any] = {
    "row_date": _DATE32,
    "security_key": _STRING,
    "source_session": _DATE32,
    "value": _FLOAT64,
}
_SEGMENT_COLUMNS = (
    "security_key",
    "segment_id",
    "anchor_session",
    "last_session",
    "admitted",
)
_SEGMENT_DTYPES: dict[str, Any] = {
    "security_key": _STRING,
    "segment_id": _INT64,
    "anchor_session": _DATE32,
    "last_session": _DATE32,
    "admitted": _BOOL,
}

_REPO_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Typed errors (fail closed)
# ---------------------------------------------------------------------------


class D7cError(Exception):
    """Base class for every D7c fail-closed error."""


class D7cInputError(D7cError, ValueError):
    """A required input is missing, unreadable or malformed."""


class D7cBindingError(D7cError, ValueError):
    """A frozen document, code or data-file hash does not match its binding."""


class D7cConsistencyError(D7cError):
    """A frozen D7c consistency invariant is violated."""


class NondeterministicTrustedInputsError(D7cError):
    """An existing output directory holds different bytes for the same ti_id."""


# ---------------------------------------------------------------------------
# Canonical JSON and hashing
# ---------------------------------------------------------------------------


def canonical_json(obj: object) -> str:
    """The frozen canonical JSON spelling: sorted keys, tight separators."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _canonical_bytes(obj: object) -> bytes:
    return (canonical_json(obj) + "\n").encode("utf-8")


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_hex(Path(path).read_bytes())


# ---------------------------------------------------------------------------
# Bindings
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FileBinding:
    """One allowlisted, hash-pinned data file relative to ``<root>``."""

    relative_path: str
    sha256: str


@dataclass(frozen=True)
class StageBinding:
    """One upstream stage's manifest binding and its allowlisted data files."""

    stage: str
    artifact_id: str
    manifest_relative_path: str
    manifest_hash_field: str
    manifest_hash: str
    manifest_sha256: str
    files: tuple[FileBinding, ...]


@dataclass(frozen=True)
class HashBinding:
    """One normative document or pinned source file bound by SHA-256."""

    name: str
    path: str
    sha256: str


@dataclass(frozen=True)
class Bindings:
    """The frozen upstream bindings; production defaults, tests inject."""

    d3: StageBinding
    d4: StageBinding
    d5: StageBinding
    dsc: StageBinding
    documents: tuple[HashBinding, ...]
    pinned_code: tuple[HashBinding, ...]

    def stages(self) -> tuple[StageBinding, StageBinding, StageBinding, StageBinding]:
        return (self.d3, self.d4, self.d5, self.dsc)

    def allowlist(self) -> frozenset[str]:
        """The only paths D7c may open through the guarded resolver."""
        allowed: set[str] = set()
        for stage in self.stages():
            allowed.add(stage.manifest_relative_path)
            for binding in stage.files:
                allowed.add(binding.relative_path)
        return frozenset(allowed)


def _documents() -> tuple[HashBinding, ...]:
    return (
        HashBinding(
            "contract",
            str(_REPO_ROOT / "docs" / "discovery_dataset_v1_contract.md"),
            "d03f35549777e5a94046f06dd0621acfc3a1dedb7a4b40adc55fdae911fa80e3",
        ),
        HashBinding(
            "d7c_clarification",
            str(_REPO_ROOT / "docs" / "discovery_dataset_v1_1_d7c_clarification.md"),
            "508d5f1ac3da3c68233c72b4b31ea75d0ec1d88be9241e6dd018cd54467b4a89",
        ),
        HashBinding(
            "d7_clarification",
            str(_REPO_ROOT / "docs" / "discovery_dataset_v1_1_d7_clarification.md"),
            "bc8e6920d31dd429beb62030123a721a7293558ba57fe8e3d4d6cd6368a96946",
        ),
        HashBinding(
            "d6_clarification",
            str(_REPO_ROOT / "docs" / "discovery_dataset_v1_1_d6_clarification.md"),
            "e3cb52a892f533c41c5c0d1d927cd54d7e200ef2a28e56e76efdf01227aa17ea",
        ),
        HashBinding(
            "classification_amendment",
            str(
                _REPO_ROOT
                / "docs"
                / "discovery_dataset_v1_1_classification_amendment.md"
            ),
            "f89a7128bb8113c6978fc6bca8bcaf6048cd0c7db1fe5ac2aaffa1c7d519c623",
        ),
        HashBinding(
            "identity_binding_clarification",
            str(
                _REPO_ROOT
                / "docs"
                / "discovery_dataset_v1_1_identity_binding_clarification.md"
            ),
            "57da1603ff0c77f19460293802d34f2b2383800e0528746dcb8fbb6b9ceedfa9",
        ),
        HashBinding(
            "d6_task",
            str(_REPO_ROOT / "worker_tasks" / "dataset_v1" / "task-d6-universe.md"),
            "25927941d9ec4860edbd3a9e5511d6ef4f27d5b5189a183c6bca8e972653fe18",
        ),
    )


def _pinned_code() -> tuple[HashBinding, ...]:
    entries = (
        (
            "engine",
            "smart_beta/spec/engine.py",
            "eea3bc30dd50121865009d0bab4999dc76068c1e2fbf9f7c965413c2a443b46a",
        ),
        (
            "requirements",
            "smart_beta/spec/requirements.py",
            "e0b99194cbdb8f1c907a03958ca2b0c1ba7b877790ef562570c83b52324c57dc",
        ),
        (
            "transforms",
            "smart_beta/spec/transforms.py",
            "15708149406017a6c970d42aebef7b0e2ea9ed6b0be48182f7769006a9ff5a66",
        ),
        (
            "expression",
            "smart_beta/spec/expression.py",
            "9b027c5da83f0778d72c236327c09967172a5314d60c47bd54ea6337fe46d6f4",
        ),
        (
            "evaluator",
            "smart_beta/spec/evaluator.py",
            "aecdeba642ca23f3285b8f55565afb6edaf255322783345e22a50c20c405bcce",
        ),
        (
            "factor_spec",
            "smart_beta/spec/factor_spec.py",
            "ab6c65d312d6fe1e421c489e6ad8045a50e265c8f73dbe6a07738e1bf5458c56",
        ),
        (
            "corporate_actions",
            "smart_beta/pit/corporate_actions.py",
            "5d57126174e42415ad967d415195c95b9f535e8a9d6a2e263bf321b3408ceffb",
        ),
        (
            "calendar_source",
            "smart_beta/vendors/tiingo/calendar_source.py",
            "79bca35ddd46f87102e90f6ce0c2d51a8b0d16f1fe9caf56b7674b43fce8e23f",
        ),
        (
            "classification",
            "smart_beta/dataset_v1/classification.py",
            "e03f5611bc91419c34429619ab62c8d89218dce1d693075c0fec3f1ad6a6bfda",
        ),
        (
            "reserve_firewall",
            "smart_beta/dataset_v1/reserve_firewall.py",
            "4b8a74b5f950bd3bdf58155bbfae32401753f2bed45d5031860d6edb1d493e15",
        ),
    )
    return tuple(
        HashBinding(name, str(_REPO_ROOT / path), sha)
        for name, path, sha in entries
    )


def default_bindings() -> Bindings:
    """The frozen production bindings (task spec §2, exact values)."""
    return Bindings(
        d3=StageBinding(
            stage="D3",
            artifact_id="n-f874d3ecb61374d4",
            manifest_relative_path="normalized/n-f874d3ecb61374d4/build.json",
            manifest_hash_field="normalized_hash",
            manifest_hash=(
                "f93093a8b8fa07521191d687560c2c26ab16630ebeed35d68aa12867def47fb3"
            ),
            manifest_sha256=(
                "f11d9b05d5cbe2a736d991ea90d4b56f469f855f33b4168a4a4af14b87666450"
            ),
            files=(
                FileBinding(
                    "normalized/n-f874d3ecb61374d4/observations.parquet",
                    "557001d0e9c617befd3c036683f171094e86e19badb760098f40c08f3f56596e",
                ),
                FileBinding(
                    "normalized/n-f874d3ecb61374d4/ca_events.parquet",
                    "afd4a2986c934d84c2b23eaf68f23ee18746e7c929c6b8625b6c9911e4a2b109",
                ),
            ),
        ),
        d4=StageBinding(
            stage="D4",
            artifact_id="i-a21f1e46621dee94",
            manifest_relative_path="derived/identity/i-a21f1e46621dee94/identity.json",
            manifest_hash_field="identity_hash",
            manifest_hash=(
                "1023dbca39fd061d52fd26eeb6302f4a8dec467a8ea996b1903da801306ae730"
            ),
            manifest_sha256=(
                "b8ed3d23979f8cbb9f320fae96a8cea968b16332fd1000fa1cdedca15df43aa4"
            ),
            files=(
                FileBinding(
                    "derived/identity/i-a21f1e46621dee94/identity_assignment.parquet",
                    "fee4614009095773f75fb832343a5fa9b87b9de1cf363125567222b08fad3f8b",
                ),
            ),
        ),
        d5=StageBinding(
            stage="D5",
            artifact_id="q-5d1028655f8c82e1",
            manifest_relative_path="derived/qc/q-5d1028655f8c82e1/qc_report.json",
            manifest_hash_field="qc_hash",
            manifest_hash=(
                "3d63e51b983096f0ae579cd1e382c613d9329d239c3f848cc785dee2d61cdfc3"
            ),
            manifest_sha256=(
                "16c582900b77a328ffaee8fdb78b8bc4b7920a0b48ebbc98f7a2e7e62c769ae1"
            ),
            files=(
                FileBinding(
                    "derived/qc/q-5d1028655f8c82e1/day_flags.parquet",
                    "f4010ebeabf5cfae2684f3658a5f19f922f15e2c8898410049f2148440cf7891",
                ),
            ),
        ),
        dsc=StageBinding(
            stage="DSC",
            artifact_id="c-27b6dabd0db0cb15",
            manifest_relative_path=(
                "derived/classification/c-27b6dabd0db0cb15/dsc.json"
            ),
            manifest_hash_field="dsc_hash",
            manifest_hash=(
                "2ee4940c14a85048fddd9e3e675c3e9a6f40a11c8887192a5e61939adfc62b60"
            ),
            manifest_sha256=(
                "23877643018f28b4776e092be1c53d048f0c51a810544f073f9a68f57abcc030"
            ),
            files=(
                FileBinding(
                    "derived/classification/c-27b6dabd0db0cb15/class_timeline.parquet",
                    "e70d5c91b3f7bce6b5dee3337d47ec3f0c5e7700b35300d4ef3fc8e48b82caeb",
                ),
                FileBinding(
                    "derived/classification/c-27b6dabd0db0cb15/policy_timeline.parquet",
                    "988087c5c0a8daf5a2674eb4b4b8db7a0e2fce141478e34b2d63d20bfb651c8e",
                ),
            ),
        ),
        documents=_documents(),
        pinned_code=_pinned_code(),
    )


DEFAULT_BINDINGS: Bindings = default_bindings()


def _bindings_payload(bindings: Bindings) -> dict[str, Any]:
    """The canonical, hashable summary of every bound hash."""
    return {
        "stages": [
            {
                "stage": stage.stage,
                "artifact_id": stage.artifact_id,
                "manifest_relative_path": stage.manifest_relative_path,
                "manifest_hash_field": stage.manifest_hash_field,
                "manifest_hash": stage.manifest_hash,
                "manifest_sha256": stage.manifest_sha256,
                "files": [
                    {"relative_path": f.relative_path, "sha256": f.sha256}
                    for f in stage.files
                ],
            }
            for stage in bindings.stages()
        ],
        "documents": {
            doc.name: doc.sha256
            for doc in sorted(bindings.documents, key=lambda d: d.name)
        },
        "pinned_code": {
            code.name: code.sha256
            for code in sorted(bindings.pinned_code, key=lambda c: c.name)
        },
    }


def _compute_ti_id(bindings: Bindings, code_sha: str) -> str:
    payload = {
        "bindings": _bindings_payload(bindings),
        "trusted_inputs_sha256": code_sha,
    }
    return "ti-" + _sha256_hex(canonical_json(payload).encode("utf-8"))[:16]


# ---------------------------------------------------------------------------
# Guarded resolver
# ---------------------------------------------------------------------------


class Resolver:
    """The single guarded gateway for every D4/D5/D-SC data-file open."""

    def __init__(self, root: Path | str, bindings: Bindings):
        self._root = Path(root)
        self._bindings = bindings
        self._allow = bindings.allowlist()

    @property
    def allowlist(self) -> frozenset[str]:
        return self._allow

    def resolve(self, relative_path: str) -> Path:
        """Resolve one allowlisted path, refusing anything across the firewall."""
        raw = str(relative_path)
        pure = PurePosixPath(raw)
        if pure.is_absolute() or ".." in pure.parts:
            raise D7cInputError(f"path {raw!r} escapes <root>")
        if any(
            part in {"reserve_sealed", "raw", "dsc_evidence"} for part in pure.parts
        ):
            raise ReserveFirewallError(
                f"refusing reserve path component in {raw!r}"
            )
        if raw not in self._allow:
            raise ReserveFirewallError(f"path {raw!r} is not allowlisted")
        return self._root / pure

    def read_bytes(self, relative_path: str) -> bytes:
        path = self.resolve(relative_path)
        try:
            return path.read_bytes()
        except OSError as exc:
            raise D7cInputError(f"cannot read {path}: {exc}") from exc

    def read_parquet(
        self, relative_path: str, *, columns: Sequence[str]
    ) -> pd.DataFrame:
        path = self.resolve(relative_path)
        try:
            return pd.read_parquet(path, columns=list(columns))
        except OSError as exc:
            raise D7cInputError(f"cannot read {path}: {exc}") from exc


# ---------------------------------------------------------------------------
# Verification helpers
# ---------------------------------------------------------------------------


def _load_json(blob: bytes, what: str) -> dict:
    try:
        obj = json.loads(blob)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise D7cInputError(f"{what} is not valid JSON: {exc}") from exc
    if not isinstance(obj, dict):
        raise D7cInputError(f"{what} is not a JSON object")
    return obj


def _verify_documents(bindings: Bindings) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for doc in bindings.documents:
        try:
            blob = Path(doc.path).read_bytes()
        except OSError as exc:
            raise D7cInputError(
                f"cannot read document {doc.name} at {doc.path}: {exc}"
            ) from exc
        actual = _sha256_hex(blob)
        if actual != doc.sha256:
            raise D7cBindingError(
                f"document {doc.name} hash {actual} != bound {doc.sha256}"
            )
        hashes[doc.name] = actual
    return hashes


def _verify_pinned_code(bindings: Bindings) -> None:
    for code in bindings.pinned_code:
        try:
            blob = Path(code.path).read_bytes()
        except OSError as exc:
            raise D7cInputError(
                f"cannot read pinned code {code.name} at {code.path}: {exc}"
            ) from exc
        actual = _sha256_hex(blob)
        if actual != code.sha256:
            raise D7cBindingError(
                f"pinned code {code.name} hash {actual} != bound {code.sha256}"
            )


def _verify_stage(resolver: Resolver, stage: StageBinding) -> None:
    blob = resolver.read_bytes(stage.manifest_relative_path)
    actual_manifest = _sha256_hex(blob)
    if actual_manifest != stage.manifest_sha256:
        raise D7cBindingError(
            f"{stage.stage} manifest file {stage.manifest_relative_path} hash "
            f"{actual_manifest} != bound {stage.manifest_sha256}"
        )
    manifest = _load_json(blob, f"{stage.stage} manifest")
    declared_hash = manifest.get(stage.manifest_hash_field)
    if declared_hash != stage.manifest_hash:
        raise D7cBindingError(
            f"{stage.stage} {stage.manifest_hash_field} {declared_hash!r} != "
            f"bound {stage.manifest_hash!r}"
        )
    files = manifest.get("files_sha256")
    if not isinstance(files, dict):
        raise D7cInputError(f"{stage.stage} manifest lacks files_sha256")
    for binding in stage.files:
        declared = files.get(binding.relative_path)
        if declared != binding.sha256:
            raise D7cBindingError(
                f"{stage.stage} manifest binds {binding.relative_path} to "
                f"{declared!r}, expected {binding.sha256!r}"
            )
        actual = _sha256_hex(resolver.read_bytes(binding.relative_path))
        if actual != binding.sha256:
            raise D7cBindingError(
                f"{stage.stage} file {binding.relative_path} hash {actual} != "
                f"bound {binding.sha256}"
            )


def _accessor_bindings(bindings: Bindings) -> dict[str, AccessorFileBinding]:
    names = ("observations", "ca_events")
    return {
        name: AccessorFileBinding(binding.relative_path, binding.sha256)
        for name, binding in zip(names, bindings.d3.files)
    }


# ---------------------------------------------------------------------------
# Small parsers
# ---------------------------------------------------------------------------


def _as_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, pd.Timestamp):
        if pd.isna(value):
            return None
        return value.date()
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    if isinstance(value, float) and math.isnan(value):
        return None
    return None


def _as_str(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    if pd.isna(value):
        return None
    return str(value)


def _as_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, np.integer)):
        return int(value)
    if isinstance(value, float):
        if math.isnan(value):
            return None
        return int(value)
    return None


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Internal records
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Key:
    security_key: str
    ticker: str
    listed_start: date
    listed_end: date


@dataclass(frozen=True)
class _Row:
    raw_row_index: int
    close: float
    volume: int | None


@dataclass(frozen=True)
class _CaEvent:
    effective_date: date | None
    action_type: str | None
    knowledge_date: date | None
    adjustment_factor: float | None
    is_superseded: bool


@dataclass(frozen=True)
class _Inputs:
    observations: pd.DataFrame
    ca_events: pd.DataFrame
    identity: pd.DataFrame
    day_flags: pd.DataFrame
    class_timeline: Mapping[str, Any]
    policy_timeline: Mapping[str, Any]


# ---------------------------------------------------------------------------
# Loading and post-load reserve assertions
# ---------------------------------------------------------------------------


def _load_inputs(
    resolver: Resolver,
    bindings: Bindings,
    accessor: Any,
) -> _Inputs:
    observations = accessor.read(
        "observations",
        start=CALENDAR_START,
        end=LAST_SOURCE_DATE,
        columns=["ticker", "date", "close", "volume", "raw_row_index"],
    )
    ca_events = accessor.read(
        "ca_events",
        start=CALENDAR_START,
        end=LAST_SOURCE_DATE,
        columns=[
            "stock_id",
            "effective_date",
            "action_type",
            "knowledge_date",
            "adjustment_factor",
            "is_superseded",
        ],
    )
    identity = resolver.read_parquet(
        bindings.d4.files[0].relative_path,
        columns=["security_key", "ticker", "listed_start", "listed_end", "state"],
    )
    day_flags = resolver.read_parquet(
        bindings.d5.files[0].relative_path,
        columns=["ticker", "date", "raw_row_index", "class_or_flag", "disposition"],
    )
    class_path = resolver.resolve(bindings.dsc.files[0].relative_path)
    policy_path = resolver.resolve(bindings.dsc.files[1].relative_path)
    class_timeline = load_class_timeline(class_path)
    policy_timeline = load_policy_timeline(policy_path)
    return _Inputs(
        observations=observations,
        ca_events=ca_events,
        identity=identity,
        day_flags=day_flags,
        class_timeline=class_timeline,
        policy_timeline=policy_timeline,
    )


def _assert_before_reserve(series: pd.Series, what: str) -> None:
    if series is None or len(series) == 0:
        return
    parsed = pd.to_datetime(series, errors="coerce")
    if bool((parsed >= pd.Timestamp(RESERVE_START)).any()):
        raise ReserveFirewallError(
            f"{what} contains a date on or after {RESERVE_START}"
        )


def _post_load_assertions(inputs: _Inputs) -> None:
    _assert_before_reserve(inputs.observations["date"], "D3 observations")
    _assert_before_reserve(inputs.day_flags["date"], "D5 day flags")
    _assert_before_reserve(
        inputs.ca_events["effective_date"], "D3 corporate actions"
    )
    _assert_before_reserve(
        inputs.ca_events["knowledge_date"], "D3 corporate actions"
    )
    for name, timeline in (
        ("class", inputs.class_timeline),
        ("policy", inputs.policy_timeline),
    ):
        for segments in timeline.values():
            for segment in segments:
                _assert_before_reserve(
                    pd.Series([segment.get("knowledge_date")]),
                    f"D-SC {name} timeline",
                )


# ---------------------------------------------------------------------------
# Identity columns and rows
# ---------------------------------------------------------------------------


def _identity_keys(identity: pd.DataFrame) -> list[_Key]:
    counts: dict[str, int] = {}
    for record in identity.to_dict("records"):
        key = _as_str(record["security_key"])
        if key is None:
            continue
        counts[key] = counts.get(key, 0) + 1

    keys: list[_Key] = []
    for record in identity.to_dict("records"):
        key = _as_str(record["security_key"])
        if key is None or counts[key] != 1:
            continue
        if _as_str(record["state"]) != STATE_INTERVAL_ASSIGNED:
            continue
        ticker = _as_str(record["ticker"])
        listed_start = _as_date(record["listed_start"])
        listed_end = _as_date(record["listed_end"])
        if ticker is None or listed_start is None or listed_end is None:
            raise D7cConsistencyError(
                f"identity key {key!r} lacks ticker/listed_start/listed_end"
            )
        keys.append(
            _Key(
                security_key=key,
                ticker=ticker,
                listed_start=listed_start,
                listed_end=listed_end,
            )
        )
    keys.sort(key=lambda k: k.security_key)
    return keys


def _index_rows(
    observations: pd.DataFrame, day_flags: pd.DataFrame
) -> tuple[dict[str, dict[date, list[_Row]]], set[tuple[str, date, int]]]:
    unusable: set[tuple[str, date, int]] = set()
    for record in day_flags.to_dict("records"):
        ticker = _as_str(record["ticker"])
        d = _as_date(record["date"])
        idx = _as_int(record["raw_row_index"])
        if ticker is None or d is None or idx is None:
            continue
        klass = _as_str(record["class_or_flag"])
        disposition = _as_str(record["disposition"])
        if (
            disposition == DISPOSITION_CORRUPT
            or klass in CORRUPT_FLAGS
            or klass == CLASS_INTERVAL_MISMATCH
            or klass == CLASS_VENDOR_NULL
        ):
            unusable.add((ticker, d, idx))

    rows: dict[str, dict[date, list[_Row]]] = {}
    for record in observations.to_dict("records"):
        ticker = _as_str(record["ticker"])
        d = _as_date(record["date"])
        idx = _as_int(record["raw_row_index"])
        if ticker is None or d is None or idx is None:
            continue
        close = _as_float(record["close"])
        volume = _as_int(record["volume"])
        rows.setdefault(ticker, {}).setdefault(d, []).append(
            _Row(
                raw_row_index=idx,
                close=0.0 if close is None else close,
                volume=volume,
            )
        )
    return rows, unusable


def _index_ca_events(ca_events: pd.DataFrame) -> dict[str, list[_CaEvent]]:
    by_ticker: dict[str, list[_CaEvent]] = {}
    for record in ca_events.to_dict("records"):
        ticker = _as_str(record["stock_id"])
        if ticker is None:
            continue
        by_ticker.setdefault(ticker, []).append(
            _CaEvent(
                effective_date=_as_date(record["effective_date"]),
                action_type=_as_str(record["action_type"]),
                knowledge_date=_as_date(record["knowledge_date"]),
                adjustment_factor=_as_float(record["adjustment_factor"]),
                is_superseded=bool(record["is_superseded"]),
            )
        )
    return by_ticker


# ---------------------------------------------------------------------------
# Corporate actions
# ---------------------------------------------------------------------------


def _member_ca_events(
    events: Sequence[_CaEvent], lower: date, upper: date
) -> list[_CaEvent]:
    selected: list[_CaEvent] = []
    for event in events:
        if event.effective_date is None:
            continue
        if not (lower <= event.effective_date <= upper):
            continue
        if event.is_superseded:
            raise D7cConsistencyError(
                f"corporate action dated {event.effective_date} is superseded"
            )
        if event.knowledge_date != event.effective_date:
            raise D7cConsistencyError(
                f"corporate action dated {event.effective_date} has "
                f"knowledge_date {event.knowledge_date}"
            )
        if (
            event.adjustment_factor is None
            or not math.isfinite(event.adjustment_factor)
            or event.adjustment_factor <= 0
        ):
            raise D7cConsistencyError(
                f"corporate action dated {event.effective_date} has invalid "
                f"adjustment_factor {event.adjustment_factor!r}"
            )
        selected.append(event)
    return selected


def _validate_ca_sessions(
    events: Sequence[_CaEvent], sessions: frozenset[date]
) -> None:
    for event in events:
        assert event.effective_date is not None
        if event.effective_date not in sessions:
            raise D7cConsistencyError(
                f"corporate action dated {event.effective_date} is not a "
                "trusted session (unmatched fails closed)"
            )


def _adjusted_returns(
    key: _Key,
    valid_returns: Mapping[date, float],
    ca_events: Sequence[_CaEvent],
) -> dict[date, float]:
    if not valid_returns:
        return {}
    ordered = sorted(valid_returns)
    raw_panel = pd.DataFrame(
        {
            "date": pd.to_datetime(ordered),
            "stock_id": pd.Series([key.security_key] * len(ordered), dtype="string"),
            "raw_ret": np.array(
                [valid_returns[d] for d in ordered], dtype=np.float64
            ),
        }
    )
    if ca_events:
        ca_panel = pd.DataFrame(
            {
                "stock_id": pd.Series(
                    [key.security_key] * len(ca_events), dtype="string"
                ),
                "effective_date": pd.to_datetime(
                    [event.effective_date for event in ca_events]
                ),
                "action_type": pd.Series(
                    [event.action_type for event in ca_events], dtype="string"
                ),
                "knowledge_date": pd.to_datetime(
                    [event.knowledge_date for event in ca_events]
                ),
                "adjustment_factor": np.array(
                    [event.adjustment_factor for event in ca_events],
                    dtype=np.float64,
                ),
                "is_superseded": pd.Series([False] * len(ca_events), dtype=bool),
            }
        )
    else:
        ca_panel = pd.DataFrame(
            {
                "stock_id": pd.Series([], dtype="string"),
                "effective_date": pd.Series([], dtype="datetime64[ns]"),
                "action_type": pd.Series([], dtype="string"),
                "knowledge_date": pd.Series([], dtype="datetime64[ns]"),
                "adjustment_factor": pd.Series([], dtype="float64"),
                "is_superseded": pd.Series([], dtype=bool),
            }
        )
    settings = replace(DEFAULT_SETTINGS, pit_availability_buffer_days=PIT_BUFFER_DAYS)
    adjusted = compute_adjusted_returns(
        raw_panel, ca_panel, as_of=AS_OF, calendar=None, settings=settings
    )
    result: dict[date, float] = {}
    for record in adjusted.to_dict("records"):
        d = _as_date(record["date"])
        value = _as_float(record["adj_ret"])
        if d is not None and value is not None:
            result[d] = value
    return result


# ---------------------------------------------------------------------------
# Per-key primitives and TRI segments
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _KeyPrimitives:
    returns: dict[date, float]
    dollar_volume: dict[date, float]
    tri: dict[date, float]
    segments: list[tuple[int, date, date, bool]]
    valid_returns: set[date]


def _usable_rows(
    rows: Sequence[_Row], ticker: str, d: date, unusable: set[tuple[str, date, int]]
) -> list[_Row]:
    usable: list[_Row] = []
    for row in rows:
        if (ticker, d, row.raw_row_index) in unusable:
            continue
        if not (row.close > 0):
            continue
        usable.append(row)
    return usable


def _compute_segments(
    interval: Sequence[date],
    usable: Mapping[date, _Row],
    returns: Mapping[date, float],
) -> tuple[list[tuple[int, date, date, bool]], dict[date, float]]:
    n = len(interval)
    valid = [interval[j] in returns for j in range(n)]
    segments: list[tuple[int, date, date, bool]] = []
    tri: dict[date, float] = {}
    search = 0
    seg_id = 0
    while search < n:
        anchor = None
        for j in range(search, n - 1):
            if interval[j] in usable and valid[j + 1]:
                anchor = j
                break
        if anchor is None:
            break
        k = anchor + 1
        while k < n and valid[k]:
            k += 1
        seg_id += 1
        last = interval[k - 1]
        if seg_id == 1:
            level = 1.0
            tri[interval[anchor]] = 1.0
            for j in range(anchor + 1, k):
                level = level * (1.0 + returns[interval[j]])
                tri[interval[j]] = level
        segments.append((seg_id, interval[anchor], last, seg_id == 1))
        search = k if k < n else n
    return segments, tri


def _primitive_session(
    row_date: date, sessions: Sequence[date], pos: Mapping[date, int]
) -> date | None:
    """The session whose primitive ``row_date`` carries: ``prev(row_date)``."""
    i = pos[row_date]
    if i == 0:
        return None
    return sessions[i - 1]


def _compute_key_primitives(
    key: _Key,
    interval: Sequence[date],
    sessions: Sequence[date],
    pos: Mapping[date, int],
    rows_by_ticker: Mapping[str, Mapping[date, list[_Row]]],
    unusable: set[tuple[str, date, int]],
    ca_events: Sequence[_CaEvent],
) -> _KeyPrimitives:
    ticker_rows = rows_by_ticker.get(key.ticker, {})
    usable: dict[date, _Row] = {}
    for s in interval:
        session_rows = ticker_rows.get(s, [])
        candidates = _usable_rows(session_rows, key.ticker, s, unusable)
        if len(candidates) > 1:
            raise D7cConsistencyError(
                f"{key.security_key!r} has {len(candidates)} usable rows on "
                f"session {s}"
            )
        if candidates:
            usable[s] = candidates[0]

    raw_returns: dict[date, float] = {}
    for s in interval:
        p = _primitive_session(s, sessions, pos)
        if p is None:
            continue
        if s in usable and p in usable:
            raw_returns[s] = usable[s].close / usable[p].close - 1.0

    returns = _adjusted_returns(key, raw_returns, ca_events)
    for s, value in returns.items():
        if not math.isfinite(value) or value <= -1.0:
            raise D7cConsistencyError(
                f"daily_total_return {value!r} for {key.security_key!r} on "
                f"{s} is not finite and > -1"
            )

    dollar_volume: dict[date, float] = {}
    for s in interval:
        row = usable.get(s)
        if row is None:
            continue
        if row.volume is None:
            continue
        dollar_volume[s] = float(row.close) * float(row.volume)

    segments, tri = _compute_segments(interval, usable, returns)
    return _KeyPrimitives(
        returns=returns,
        dollar_volume=dollar_volume,
        tri=tri,
        segments=segments,
        valid_returns=set(returns),
    )


# ---------------------------------------------------------------------------
# Frame builders
# ---------------------------------------------------------------------------


def _build_long(
    records: Sequence[tuple[date, str, date, float]],
) -> pd.DataFrame:
    data = {
        "row_date": pd.Series(
            [r[0] for r in records], dtype=_DATE32
        ),
        "security_key": pd.Series([r[1] for r in records], dtype=_STRING),
        "source_session": pd.Series([r[2] for r in records], dtype=_DATE32),
        "value": pd.Series([r[3] for r in records], dtype="float64"),
    }
    frame = pd.DataFrame(data)
    if frame.empty:
        return pd.DataFrame(
            {
                "row_date": pd.Series([], dtype=_DATE32),
                "security_key": pd.Series([], dtype=_STRING),
                "source_session": pd.Series([], dtype=_DATE32),
                "value": pd.Series([], dtype=_FLOAT64),
            }
        )
    frame = frame.sort_values(
        ["row_date", "security_key"], kind="stable"
    ).reset_index(drop=True)
    return frame


def _build_segments(
    records: Sequence[tuple[str, int, date, date, bool]],
) -> pd.DataFrame:
    frame = pd.DataFrame(
        {
            "security_key": pd.Series([r[0] for r in records], dtype=_STRING),
            "segment_id": pd.Series([r[1] for r in records], dtype="int64"),
            "anchor_session": pd.Series([r[2] for r in records], dtype=_DATE32),
            "last_session": pd.Series([r[3] for r in records], dtype=_DATE32),
            "admitted": pd.Series([r[4] for r in records], dtype=bool),
        }
    )
    if frame.empty:
        return pd.DataFrame(
            {
                "security_key": pd.Series([], dtype=_STRING),
                "segment_id": pd.Series([], dtype=_INT64),
                "anchor_session": pd.Series([], dtype=_DATE32),
                "last_session": pd.Series([], dtype=_DATE32),
                "admitted": pd.Series([], dtype=_BOOL),
            }
        )
    frame = frame.sort_values(
        ["security_key", "segment_id"], kind="stable"
    ).reset_index(drop=True)
    return frame


# ---------------------------------------------------------------------------
# Output writing
# ---------------------------------------------------------------------------


def _write_outputs(
    root: Path,
    ti_id: str,
    frames: Mapping[str, pd.DataFrame],
    segments: pd.DataFrame,
    content: dict[str, Any],
) -> dict[str, Any]:
    derived = root / "derived" / "trusted_inputs"
    derived.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=ti_id + ".", dir=derived))
    try:
        produced: dict[str, Path] = {}
        for name in INPUT_NAMES:
            path = stage / _INPUT_FILES[name]
            frames[name].to_parquet(
                path, engine="pyarrow", index=False, compression="zstd"
            )
            produced[f"derived/trusted_inputs/{ti_id}/{_INPUT_FILES[name]}"] = path
        segments_path = stage / SEGMENTS_FILE
        segments.to_parquet(
            segments_path, engine="pyarrow", index=False, compression="zstd"
        )
        produced[
            f"derived/trusted_inputs/{ti_id}/{SEGMENTS_FILE}"
        ] = segments_path

        files_sha256 = {
            relative: _sha256_file(path) for relative, path in produced.items()
        }
        content["files_sha256"] = files_sha256
        content["ti_hash"] = _sha256_hex(
            canonical_json(files_sha256).encode("utf-8")
        )
        (stage / MANIFEST_FILE).write_bytes(_canonical_bytes(content))

        target = derived / ti_id
        if target.exists():
            existing = sorted(p.name for p in target.iterdir())
            staged = sorted(p.name for p in stage.iterdir())
            if existing != staged or any(
                (target / name).read_bytes() != (stage / name).read_bytes()
                for name in staged
            ):
                raise NondeterministicTrustedInputsError(
                    f"existing {target} holds different bytes for {ti_id}"
                )
            shutil.rmtree(stage)
        else:
            stage.rename(target)
        return content
    finally:
        shutil.rmtree(stage, ignore_errors=True)


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------


def _capabilities(history: int) -> dict[str, DataCapability]:
    return {
        INPUT_DAILY_TOTAL_RETURN: DataCapability(
            semantic_id=INPUT_DAILY_TOTAL_RETURN,
            frequency=Frequency.DAILY,
            observation_period=ObservationPeriod.PERIOD,
            units=Unit.FRACTION,
            history=history,
            has_knowledge_date=True,
            has_positive_vintage_identity=False,
            revision_policies=frozenset({RevisionPolicy.POINT_IN_TIME}),
        ),
        INPUT_TOTAL_RETURN_INDEX: DataCapability(
            semantic_id=INPUT_TOTAL_RETURN_INDEX,
            frequency=Frequency.DAILY,
            observation_period=ObservationPeriod.INSTANT,
            units=Unit.RATIO,
            history=history,
            has_knowledge_date=True,
            has_positive_vintage_identity=False,
            revision_policies=frozenset({RevisionPolicy.POINT_IN_TIME}),
        ),
        INPUT_DOLLAR_VOLUME: DataCapability(
            semantic_id=INPUT_DOLLAR_VOLUME,
            frequency=Frequency.DAILY,
            observation_period=ObservationPeriod.PERIOD,
            units=Unit.CURRENCY,
            history=history,
            has_knowledge_date=True,
            has_positive_vintage_identity=False,
            revision_policies=frozenset({RevisionPolicy.POINT_IN_TIME}),
        ),
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def build_trusted_inputs(
    root: Path | str,
    *,
    bindings: Bindings | None = None,
    calendar: Any | None = None,
    accessor: Any | None = None,
) -> dict[str, Any]:
    """Build the D7c artifact and return its manifest content."""
    bindings = bindings if bindings is not None else DEFAULT_BINDINGS
    if not isinstance(bindings, Bindings):
        raise D7cInputError("bindings must be a Bindings value")
    calendar = (
        calendar
        if calendar is not None
        else build_nyse_calendar(CALENDAR_START, CALENDAR_END)
    )
    root = Path(root)
    resolver = Resolver(root, bindings)
    documents = _verify_documents(bindings)
    _verify_pinned_code(bindings)
    code_sha = _sha256_file(Path(__file__))
    ti_id = _compute_ti_id(bindings, code_sha)

    for stage in bindings.stages():
        _verify_stage(resolver, stage)

    if accessor is None:
        accessor = DevelopmentAccessor(root, bindings=_accessor_bindings(bindings))
    inputs = _load_inputs(resolver, bindings, accessor)
    _post_load_assertions(inputs)

    sessions = [ts.date() for ts in calendar.dates]
    pos = {d: i for i, d in enumerate(sessions)}
    grid_sessions = [
        d for d in sessions if CALENDAR_START <= d <= DEVELOPMENT_END
    ]

    keys = _identity_keys(inputs.identity)
    rows_by_ticker, unusable = _index_rows(inputs.observations, inputs.day_flags)
    ca_by_ticker = _index_ca_events(inputs.ca_events)
    session_set = frozenset(sessions)

    daily_records: list[tuple[date, str, date, float]] = []
    tri_records: list[tuple[date, str, date, float]] = []
    dv_records: list[tuple[date, str, date, float]] = []
    segment_records: list[tuple[str, int, date, date, bool]] = []
    mask_counts: dict[str, dict[str, int]] = {
        name: {
            "admitted": 0,
            "class_masked": 0,
            "policy_masked": 0,
            "invalid": 0,
            "segment_masked": 0,
        }
        for name in INPUT_NAMES
    }

    for key in keys:
        upper = min(key.listed_end, DEVELOPMENT_END)
        interval = [
            s
            for s in sessions
            if key.listed_start <= s <= upper
        ]
        if not interval:
            continue
        events = _member_ca_events(
            ca_by_ticker.get(key.ticker, []),
            key.listed_start,
            min(upper, LAST_SOURCE_DATE),
        )
        _validate_ca_sessions(events, session_set)
        primitives = _compute_key_primitives(
            key,
            interval,
            sessions,
            pos,
            rows_by_ticker,
            unusable,
            events,
        )
        for segment_id, anchor, last, admitted in primitives.segments:
            segment_records.append(
                (key.security_key, segment_id, anchor, last, admitted)
            )

        for row_date in grid_sessions:
            source_session = _primitive_session(row_date, sessions, pos)
            if source_session is None:
                continue
            class_state, _ = class_state_at(
                inputs.class_timeline, key.security_key, source_session
            )
            if class_state != CLASS_COMMON_STOCK:
                mask_counts[INPUT_DAILY_TOTAL_RETURN]["class_masked"] += 1
                mask_counts[INPUT_TOTAL_RETURN_INDEX]["class_masked"] += 1
                mask_counts[INPUT_DOLLAR_VOLUME]["class_masked"] += 1
                continue
            policy_state, _ = policy_state_at(
                inputs.policy_timeline, key.security_key, source_session
            )
            if policy_state != POLICY_NO_EXCLUSION:
                mask_counts[INPUT_DAILY_TOTAL_RETURN]["policy_masked"] += 1
                mask_counts[INPUT_TOTAL_RETURN_INDEX]["policy_masked"] += 1
                mask_counts[INPUT_DOLLAR_VOLUME]["policy_masked"] += 1
                continue

            if source_session in primitives.returns:
                daily_records.append(
                    (
                        row_date,
                        key.security_key,
                        source_session,
                        primitives.returns[source_session],
                    )
                )
                mask_counts[INPUT_DAILY_TOTAL_RETURN]["admitted"] += 1
            else:
                mask_counts[INPUT_DAILY_TOTAL_RETURN]["invalid"] += 1

            if source_session in primitives.dollar_volume:
                dv_records.append(
                    (
                        row_date,
                        key.security_key,
                        source_session,
                        primitives.dollar_volume[source_session],
                    )
                )
                mask_counts[INPUT_DOLLAR_VOLUME]["admitted"] += 1
            else:
                mask_counts[INPUT_DOLLAR_VOLUME]["invalid"] += 1

            if source_session in primitives.tri:
                tri_records.append(
                    (
                        row_date,
                        key.security_key,
                        source_session,
                        primitives.tri[source_session],
                    )
                )
                mask_counts[INPUT_TOTAL_RETURN_INDEX]["admitted"] += 1
            elif source_session in primitives.valid_returns:
                mask_counts[INPUT_TOTAL_RETURN_INDEX]["segment_masked"] += 1
            else:
                mask_counts[INPUT_TOTAL_RETURN_INDEX]["invalid"] += 1

    frames = {
        INPUT_DAILY_TOTAL_RETURN: _build_long(daily_records),
        INPUT_TOTAL_RETURN_INDEX: _build_long(tri_records),
        INPUT_DOLLAR_VOLUME: _build_long(dv_records),
    }
    for name, frame in frames.items():
        _assert_output_dates(frame, name)
    segments = _build_segments(segment_records)

    columns = [key.security_key for key in keys]
    first_row = grid_sessions[0] if grid_sessions else None
    last_row = grid_sessions[-1] if grid_sessions else None
    capabilities = _capabilities(len(grid_sessions))

    content: dict[str, Any] = {
        "schema": SCHEMA,
        "ti_id": ti_id,
        "ti_code_sha256": code_sha,
        "bindings": _bindings_payload(bindings),
        "documents": dict(documents),
        "pinned_code": {
            code.name: code.sha256 for code in bindings.pinned_code
        },
        "grid": {
            "first_row": first_row.isoformat() if first_row else None,
            "last_row": last_row.isoformat() if last_row else None,
            "row_count": len(grid_sessions),
            "columns": columns,
            "columns_sha256": _sha256_hex(
                canonical_json(columns).encode("utf-8")
            ),
        },
        "capabilities": {
            name: cap.to_dict() for name, cap in capabilities.items()
        },
        "knowledge_date_rule": KNOWLEDGE_DATE_RULE,
        "mask_counts": mask_counts,
        "certification_boundary": CERTIFICATION_BOUNDARY,
    }
    return _write_outputs(root, ti_id, frames, segments, content)


def _assert_output_dates(frame: pd.DataFrame, name: str) -> None:
    for column in ("row_date", "source_session"):
        if column in frame.columns and len(frame):
            parsed = pd.to_datetime(frame[column], errors="coerce")
            if bool((parsed >= pd.Timestamp(RESERVE_START)).any()):
                raise ReserveFirewallError(
                    f"{name} output contains a {column} on or after "
                    f"{RESERVE_START}"
                )


# ---------------------------------------------------------------------------
# Loader (artifact only)
# ---------------------------------------------------------------------------


def _grid_row_dates(manifest: Mapping[str, Any], calendar: Any) -> list[date]:
    grid = manifest.get("grid", {})
    first = grid.get("first_row")
    last = grid.get("last_row")
    if first is None or last is None:
        return []
    first_date = date.fromisoformat(first)
    last_date = date.fromisoformat(last)
    sessions = [ts.date() for ts in calendar.dates]
    return [d for d in sessions if first_date <= d <= last_date]


def _long_to_grid(
    long: pd.DataFrame, row_dates: Sequence[date], columns: Sequence[str]
) -> pd.DataFrame:
    index = pd.DatetimeIndex(pd.to_datetime(list(row_dates)), name=None)
    wide = pd.DataFrame(
        np.nan,
        index=index,
        columns=pd.Index(list(columns), dtype=object),
        dtype="float64",
    )
    if len(long) and len(columns):
        row_lookup = {d: i for i, d in enumerate(row_dates)}
        col_lookup = {c: i for i, c in enumerate(columns)}
        values = wide.to_numpy(copy=True)
        for record in long.to_dict("records"):
            d = _as_date(record["row_date"])
            key = _as_str(record["security_key"])
            value = _as_float(record["value"])
            if d is None or key is None:
                continue
            values[row_lookup[d], col_lookup[key]] = value
        wide = pd.DataFrame(
            values, index=index, columns=pd.Index(list(columns), dtype=object)
        )
    wide = wide.astype("float64")
    wide = wide.sort_index(axis=0).sort_index(axis=1)
    return wide


def load_trusted_inputs(
    root: Path | str,
    *,
    bindings: Bindings | None = None,
    calendar: Any | None = None,
) -> dict[str, TrustedInput]:
    """Load the three D7c ``TrustedInput`` values from the frozen artifact."""
    bindings = bindings if bindings is not None else DEFAULT_BINDINGS
    if not isinstance(bindings, Bindings):
        raise D7cInputError("bindings must be a Bindings value")
    calendar = (
        calendar
        if calendar is not None
        else build_nyse_calendar(CALENDAR_START, CALENDAR_END)
    )
    code_sha = _sha256_file(Path(__file__))
    ti_id = _compute_ti_id(bindings, code_sha)
    directory = Path(root) / "derived" / "trusted_inputs" / ti_id
    manifest_path = directory / MANIFEST_FILE
    if not manifest_path.is_file():
        raise D7cInputError(f"no D7c artifact at {directory}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    files_sha256 = manifest.get("files_sha256")
    if not isinstance(files_sha256, dict):
        raise D7cInputError("D7c manifest lacks files_sha256")
    for relative, expected in files_sha256.items():
        actual = _sha256_file(Path(root) / relative)
        if actual != expected:
            raise D7cBindingError(
                f"D7c artifact file {relative} hash {actual} != {expected}"
            )
    expected_ti_hash = _sha256_hex(canonical_json(files_sha256).encode("utf-8"))
    if manifest.get("ti_hash") != expected_ti_hash:
        raise D7cBindingError("D7c manifest ti_hash does not match files_sha256")

    row_dates = _grid_row_dates(manifest, calendar)
    columns = list(manifest.get("grid", {}).get("columns", []))
    capabilities = _capabilities(len(row_dates))
    result: dict[str, TrustedInput] = {}
    for name in INPUT_NAMES:
        long = pd.read_parquet(directory / _INPUT_FILES[name])
        wide = _long_to_grid(long, row_dates, columns)
        result[name] = TrustedInput(
            values=wide,
            capability=capabilities[name],
            evidence_class=EvidenceClass.LIVE_RECORDED,
            vintage_evidence=None,
        )
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build the D7c development TrustedInput artifact."
    )
    parser.add_argument("--root", required=True, help="data_store root")
    args = parser.parse_args(argv)
    content = build_trusted_inputs(args.root)
    print(content["ti_id"])
    print(content["ti_hash"])
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
