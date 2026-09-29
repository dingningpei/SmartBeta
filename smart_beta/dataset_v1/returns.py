"""Discovery Dataset v1.1 — D7a development observed-return evidence.

D7a stores **factual observed evidence** for the 90 development formations of
the Dataset-v1.1 build: the contract-§8 daily total returns, per-session
status, per-``(formation, member, h)`` path evidence for every ``h = 1…63``,
retrospective D4 terminal metadata, calendar endpoints / purge metadata and
provenance.

It stores **no** compounded forward return (P7-B owns compounding), **no**
``T0``/``TS``/``τ``/synthetic-proceeds value, **no** factor value / rank /
leg / quantile / predicted-high-low assignment, and **no** reserve-period
market observation or exact ``listed_end`` after 2023-06-30.

Governing authority (frozen), in precedence order: ``docs/
discovery_dataset_v1_contract.md`` §§7, 8, 9, 10, 11, 12, 14; the Dataset-v1.1
D7 clarification; the Dataset-v1.1 D6 clarification; the Dataset-v1.1 D6
duplicate-``security_key`` clarification; ``worker_tasks/phase7/
phase7-plan.md`` §8.1; and ``worker_tasks/dataset_v1/task-d6-universe.md`` §5.

Reserve firewall (mechanical, fail closed): every data file is opened through
one :class:`Resolver`, which refuses any path whose components include
``reserve_sealed``, ``raw`` or ``dsc_evidence``, and any path not in the
allowlist derived from the frozen :class:`Bindings`.  After loading, every
observation, day-flag and corporate-action date must be earlier than
2023-07-03 and every ``universe_development`` formation date must be on or
before 2023-06-30.  Before writing, every stored date except the calendar-only
``horizons.endpoint`` must be earlier than 2023-07-03.

The module never makes a provider or network call, never reads a credential
value, never reads the real ``data_store`` (tests inject synthetic bindings
and ``tmp_path`` fixtures), never reads a vendor ``adj*`` field, and never
re-derives a D6 decision.
"""

from __future__ import annotations

import argparse
import bisect
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
from smart_beta.pit.corporate_actions import compute_adjusted_returns
from smart_beta.vendors.tiingo.calendar_source import build_nyse_calendar

__all__ = [
    "SCHEMA",
    "RESERVE_START",
    "DEVELOPMENT_END",
    "CALENDAR_START",
    "CALENDAR_END",
    "HMAX",
    "AS_OF",
    "PIT_BUFFER_DAYS",
    "PARTITION_DEVELOPMENT",
    "STATUS_VALID",
    "STATUS_NO_ROW",
    "STATUS_ROW_UNUSABLE",
    "STATUS_PREV_NO_ROW",
    "STATUS_PREV_ROW_UNUSABLE",
    "STATUS_POST_TERMINAL",
    "STATE_PURGED_RESERVE_CROSSING",
    "STATE_EMPTY_OBSERVED_PATH_IDENTITY",
    "STATE_COMPLETE",
    "STATE_INCOMPLETE",
    "LISTING_ENDS_WITHIN_DEVELOPMENT",
    "LISTING_CONTINUES_BEYOND_DEVELOPMENT_PERIOD",
    "TERMINAL_EVIDENCE_SOURCE",
    "FileBinding",
    "StageBinding",
    "DocumentBinding",
    "Bindings",
    "Resolver",
    "D7Error",
    "ReturnsInputError",
    "ReturnsBindingError",
    "ReserveFirewallError",
    "D7ConsistencyError",
    "NondeterministicReturnsError",
    "canonical_json",
    "default_bindings",
    "run_returns",
    "main",
]


# ---------------------------------------------------------------------------
# Frozen constants
# ---------------------------------------------------------------------------

SCHEMA = "discovery-dataset-v1-returns-v1"

#: Fixed start of the sealed historical reserve (contract §§11.2, 12).
RESERVE_START = date(2023, 7, 3)
#: Last development formation / last exposed development date.
DEVELOPMENT_END = date(2023, 6, 30)
#: Trusted-calendar bounds (task spec §4).
CALENDAR_START = date(2015, 1, 2)
CALENDAR_END = date(2027, 12, 31)
#: Maximum evaluation horizon in trusted NYSE sessions.
HMAX = 63
#: The corporate-action knowledge cutoff and buffer (task spec §5.3).
AS_OF = date(2023, 6, 30)
PIT_BUFFER_DAYS = 0

PARTITION_DEVELOPMENT = "DEVELOPMENT"

#: Session-status vocabulary (task spec §5.3, first matching row).
STATUS_POST_TERMINAL = "POST_TERMINAL"
STATUS_NO_ROW = "NO_ROW"
STATUS_ROW_UNUSABLE = "ROW_UNUSABLE"
STATUS_PREV_NO_ROW = "PREV_NO_ROW"
STATUS_PREV_ROW_UNUSABLE = "PREV_ROW_UNUSABLE"
STATUS_VALID = "VALID"

#: Label-state vocabulary (task spec §6).
STATE_PURGED_RESERVE_CROSSING = "PURGED_RESERVE_CROSSING"
STATE_EMPTY_OBSERVED_PATH_IDENTITY = "EMPTY_OBSERVED_PATH_IDENTITY"
STATE_COMPLETE = "COMPLETE"
STATE_INCOMPLETE = "INCOMPLETE"

LISTING_ENDS_WITHIN_DEVELOPMENT = "ENDS_WITHIN_DEVELOPMENT"
LISTING_CONTINUES_BEYOND_DEVELOPMENT_PERIOD = (
    "CONTINUES_BEYOND_DEVELOPMENT_PERIOD"
)
TERMINAL_EVIDENCE_SOURCE = "D4_RETROSPECTIVE_LISTED_END"

#: D5 dispositions / classes (reused from D6 §5, not re-derived).
DISPOSITION_CORRUPT = "CORRUPT"
CLASS_INTERVAL_MISMATCH = "INTERVAL_MISMATCH"
CLASS_VENDOR_NULL = "VENDOR_NULL"
CLASS_CORP_ACTION_SUSPECT = "CORP_ACTION_SUSPECT"
#: The five CORRUPT observation flags (§5.2).
CORRUPT_FLAGS = frozenset(
    {
        "OHLC_INCONSISTENT",
        "NONPOSITIVE_PRICE",
        "MALFORMED_CA",
        "DUPLICATE_DATE",
        "OFF_CALENDAR_ROW",
    }
)

#: D4 identity state admissible for a member (task spec §5.1).
STATE_INTERVAL_ASSIGNED = "INTERVAL_ASSIGNED"

#: §8 D7b obligations, recorded for provenance (not implemented here).
D7B_OBLIGATIONS = (
    "remove crosses_reserve labels before calling P7-B, so they are never "
    "reported as insufficient_forward_window",
    "build the P7-B return panel on the full trusted-session date axis, so "
    "that P7-B's h counts trusted sessions",
    "never treat POST_TERMINAL or EMPTY_OBSERVED_PATH_IDENTITY as missing",
    "apply T0/TS only at evaluation time, after leg assignment, with "
    "R_scenario = (1 + R_obs)(1 + tau) - 1",
    "decide explicitly how terminal-affected but INCOMPLETE paths enter the "
    "statistics; D7a takes no position",
)

CERTIFICATION_BOUNDARY = (
    "D7a deterministically reconstructs, from the bound D3/D4/D5/D6 artifacts "
    "and only development-period observations, the contract-§8 daily total "
    "returns (raw close-to-close between consecutive usable trusted sessions, "
    "adjusted by the Phase-4B corporate-action table on exact dates) for every "
    "D6 development top-1000 formation member. For every formation, member and "
    "horizon 1-63 trusted sessions, it records factual path evidence: "
    "completeness; typed invalid sessions; POST_TERMINAL sessions; "
    "terminal-affected status from the retrospective D4 listed_end, including "
    "listed_end == f; calendar endpoints and reserve-crossing purge flags. It "
    "never infers terminal events from missing data, never reads reserve "
    "observations, never drops a formation member and stores no scenario "
    "return. Not claimed: alpha, predictive power or factor validity; "
    "investability, transaction-cost realism, capacity or borrow; certified "
    "terminal proceeds or delisting returns (DELISTING_RETURN_NOT_CERTIFIED); "
    "historical delisting-knowledge timing; issuer-identity knowledge timing/"
    "continuity or identifier continuity (NOT CERTIFIED); complete survivorship "
    "elimination; complex corporate actions beyond the specimen-level "
    "certification; production trading readiness; confirmation evidence, "
    "reserve access or reserve consumption; any compounded forward return, T0 "
    "or TS result."
)

_STRING = pd.ArrowDtype(pa.string())
_DATE32 = pd.ArrowDtype(pa.date32())
_INT64 = pd.ArrowDtype(pa.int64())
_FLOAT64 = pd.ArrowDtype(pa.float64())
_BOOL = pd.ArrowDtype(pa.bool_())

#: Relative output filenames under ``derived/returns/<returns_id>/``.
MEMBERS_FILE = "members.parquet"
HORIZONS_FILE = "horizons.parquet"
DAILY_FILE = "daily_returns.parquet"
LABELS_FILE = "labels.parquet"
REPORT_FILE = "returns.json"

_MEMBERS_COLUMNS = (
    "formation_date",
    "security_key",
    "ticker",
    "rank",
    "top_200",
    "top_500",
    "top_1000",
    "listed_end_dev",
    "listing_status",
    "terminal_evidence_source",
)
_MEMBERS_DTYPES: dict[str, Any] = {
    "formation_date": _DATE32,
    "security_key": _STRING,
    "ticker": _STRING,
    "rank": _INT64,
    "top_200": _BOOL,
    "top_500": _BOOL,
    "top_1000": _BOOL,
    "listed_end_dev": _DATE32,
    "listing_status": _STRING,
    "terminal_evidence_source": _STRING,
}

_HORIZONS_COLUMNS = ("formation_date", "h", "endpoint", "crosses_reserve")
_HORIZONS_DTYPES: dict[str, Any] = {
    "formation_date": _DATE32,
    "h": _INT64,
    "endpoint": _DATE32,
    "crosses_reserve": _BOOL,
}

_DAILY_COLUMNS = (
    "security_key",
    "date",
    "prev_session",
    "session_status",
    "daily_total_return",
    "ca_event_count",
    "corp_action_suspect",
)
_DAILY_DTYPES: dict[str, Any] = {
    "security_key": _STRING,
    "date": _DATE32,
    "prev_session": _DATE32,
    "session_status": _STRING,
    "daily_total_return": _FLOAT64,
    "ca_event_count": _INT64,
    "corp_action_suspect": _BOOL,
}

_LABELS_COLUMNS = (
    "formation_date",
    "security_key",
    "h",
    "endpoint",
    "label_state",
    "terminal_affected",
    "observed_path_complete",
    "n_observed_sessions",
    "n_post_terminal_sessions",
    "n_invalid_sessions",
    "observed_segment_end",
    "first_invalid_session",
    "first_invalid_status",
)
_LABELS_DTYPES: dict[str, Any] = {
    "formation_date": _DATE32,
    "security_key": _STRING,
    "h": _INT64,
    "endpoint": _DATE32,
    "label_state": _STRING,
    "terminal_affected": _BOOL,
    "observed_path_complete": _BOOL,
    "n_observed_sessions": _INT64,
    "n_post_terminal_sessions": _INT64,
    "n_invalid_sessions": _INT64,
    "observed_segment_end": _DATE32,
    "first_invalid_session": _DATE32,
    "first_invalid_status": _STRING,
}

_DOCUMENT_NAMES = frozenset(
    {
        "contract",
        "d7_clarification",
        "d6_clarification",
        "d6_duplicate_key_clarification",
        "phase7_plan",
        "d6_task",
    }
)

_REPO_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Binding values
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
class DocumentBinding:
    """One normative document that D7a's authority list hash-binds."""

    name: str
    path: str
    sha256: str


@dataclass(frozen=True)
class Bindings:
    """The frozen upstream bindings; production defaults, tests inject."""

    d3: StageBinding
    d4: StageBinding
    d5: StageBinding
    d6: StageBinding
    documents: tuple[DocumentBinding, ...]

    def stages(self) -> tuple[StageBinding, StageBinding, StageBinding, StageBinding]:
        return (self.d3, self.d4, self.d5, self.d6)

    def allowlist(self) -> frozenset[str]:
        """The only paths D7a may open, relative to ``<root>``."""
        allowed: set[str] = set()
        for stage in self.stages():
            allowed.add(stage.manifest_relative_path)
            for binding in stage.files:
                allowed.add(binding.relative_path)
        return frozenset(allowed)


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
        d6=StageBinding(
            stage="D6",
            artifact_id="u-6150bc6ac2866b7f",
            manifest_relative_path="derived/universe/u-6150bc6ac2866b7f/universe.json",
            manifest_hash_field="universe_hash",
            manifest_hash=(
                "6e8539b7acb4ed4559846234b0b26e6f1d4e6d34f7e0a5d1e2290134d563ed87"
            ),
            manifest_sha256=(
                "8e5e4ecd1729c2bbf75b1ac1bdd5d497f9f2a360061edb407fc323feb433d737"
            ),
            files=(
                FileBinding(
                    "derived/universe/u-6150bc6ac2866b7f/universe_development.parquet",
                    "f933a67ff8c83ad9f6c7937a76136d93419cedb0ec79cec617df8205b3d38d8b",
                ),
                FileBinding(
                    "derived/universe/u-6150bc6ac2866b7f/formations.parquet",
                    "dcd547fe6f6e8c7e70c298a9821ad7d14ee801c959a7aeacaa5aba5803cc35a1",
                ),
            ),
        ),
        documents=(
            DocumentBinding(
                "contract",
                str(_REPO_ROOT / "docs" / "discovery_dataset_v1_contract.md"),
                "d03f35549777e5a94046f06dd0621acfc3a1dedb7a4b40adc55fdae911fa80e3",
            ),
            DocumentBinding(
                "d7_clarification",
                str(_REPO_ROOT / "docs" / "discovery_dataset_v1_1_d7_clarification.md"),
                "bc8e6920d31dd429beb62030123a721a7293558ba57fe8e3d4d6cd6368a96946",
            ),
            DocumentBinding(
                "d6_clarification",
                str(_REPO_ROOT / "docs" / "discovery_dataset_v1_1_d6_clarification.md"),
                "e3cb52a892f533c41c5c0d1d927cd54d7e200ef2a28e56e76efdf01227aa17ea",
            ),
            DocumentBinding(
                "d6_duplicate_key_clarification",
                str(
                    _REPO_ROOT
                    / "docs"
                    / "discovery_dataset_v1_1_d6_duplicate_key_clarification.md"
                ),
                "7d34c6037040f8998966221fd1a68db1e9f2f9e74004d746081464035ebb22a0",
            ),
            DocumentBinding(
                "phase7_plan",
                str(_REPO_ROOT / "worker_tasks" / "phase7" / "phase7-plan.md"),
                "8b2009533ee00e8a04e0c65b6853711f01387592e42f60c0b97170d23183a13e",
            ),
            DocumentBinding(
                "d6_task",
                str(_REPO_ROOT / "worker_tasks" / "dataset_v1" / "task-d6-universe.md"),
                "25927941d9ec4860edbd3a9e5511d6ef4f27d5b5189a183c6bca8e972653fe18",
            ),
        ),
    )


DEFAULT_BINDINGS = default_bindings()


# ---------------------------------------------------------------------------
# Typed errors (fail closed)
# ---------------------------------------------------------------------------


class D7Error(Exception):
    """Base class for every D7a fail-closed error."""


class ReturnsInputError(D7Error, ValueError):
    """A required input is missing, unreadable or malformed."""


class ReturnsBindingError(D7Error, ValueError):
    """A binding hash does not match (fail closed, no output written)."""


class ReserveFirewallError(D7Error, ValueError):
    """A read on or beyond the reserve boundary was attempted."""


class D7ConsistencyError(ReturnsInputError):
    """The inputs are internally inconsistent in a way D7a cannot resolve."""


class NondeterministicReturnsError(D7Error):
    """An existing output directory holds different bytes for the same id."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def canonical_json(obj: object) -> str:
    """The frozen canonical JSON spelling: sorted keys, tight separators, ASCII."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _canonical_bytes(obj: object) -> bytes:
    return (canonical_json(obj) + "\n").encode("utf-8")


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_hex(Path(path).read_bytes())


def _as_date(value: Any) -> date | None:
    """Normalize a parquet date-like value to a ``datetime.date`` or ``None``."""
    if value is None or value is pd.NaT:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, pd.Timestamp):
        return value.date()
    raise ReturnsInputError(f"unexpected date value {value!r}")


def _as_str(value: Any) -> str | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return str(value)


def _as_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return int(value)


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        fvalue = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(fvalue):
        return None
    return fvalue


def _build_frame(
    records: Sequence[Sequence[Any]],
    columns: Sequence[str],
    dtypes: Mapping[str, Any],
) -> pd.DataFrame:
    data = {
        column: pd.Series(
            [record[index] for record in records], dtype=dtypes[column]
        )
        for index, column in enumerate(columns)
    }
    return pd.DataFrame(data)


# ---------------------------------------------------------------------------
# Reserve firewall resolver
# ---------------------------------------------------------------------------


class Resolver:
    """The single guarded gateway for every D7a data-file open."""

    def __init__(self, root: Path | str, bindings: Bindings):
        self._root = Path(root)
        self._bindings = bindings
        self._allow = bindings.allowlist()

    @property
    def allowlist(self) -> frozenset[str]:
        return self._allow

    def resolve(self, relative_path: str) -> Path:
        """Resolve one allowlisted path, refusing anything across the firewall.

        Raises :class:`ReserveFirewallError` if any path component is
        ``reserve_sealed``, ``raw`` or ``dsc_evidence``, or if
        ``relative_path`` is not derived from the bindings.
        """
        raw = str(relative_path)
        pure = PurePosixPath(raw)
        if pure.is_absolute() or ".." in pure.parts:
            raise ReturnsInputError(f"path {raw!r} escapes <root>")
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
            raise ReturnsInputError(f"cannot read {path}: {exc}") from exc

    def read_parquet(
        self, relative_path: str, *, columns: Sequence[str]
    ) -> pd.DataFrame:
        path = self.resolve(relative_path)
        try:
            return pd.read_parquet(path, columns=list(columns))
        except OSError as exc:
            raise ReturnsInputError(f"cannot read {path}: {exc}") from exc


def _load_json(blob: bytes, what: str) -> dict:
    try:
        obj = json.loads(blob)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReturnsInputError(f"{what} is not valid JSON: {exc}") from exc
    if not isinstance(obj, dict):
        raise ReturnsInputError(f"{what} is not a JSON object")
    return obj


def _verify_documents(bindings: Bindings) -> dict[str, str]:
    names = {doc.name for doc in bindings.documents}
    if names != _DOCUMENT_NAMES:
        raise ReturnsInputError(
            f"document bindings must be exactly {sorted(_DOCUMENT_NAMES)}, "
            f"got {sorted(names)}"
        )
    hashes: dict[str, str] = {}
    for doc in bindings.documents:
        try:
            blob = Path(doc.path).read_bytes()
        except OSError as exc:
            raise ReturnsInputError(
                f"cannot read document {doc.name} at {doc.path}: {exc}"
            ) from exc
        actual = _sha256_hex(blob)
        if actual != doc.sha256:
            raise ReturnsBindingError(
                f"document {doc.name} hash {actual} != bound {doc.sha256}"
            )
        hashes[doc.name] = actual
    return hashes


def _verify_stage(resolver: Resolver, stage: StageBinding) -> None:
    blob = resolver.read_bytes(stage.manifest_relative_path)
    actual_manifest = _sha256_hex(blob)
    if actual_manifest != stage.manifest_sha256:
        raise ReturnsBindingError(
            f"{stage.stage} manifest file {stage.manifest_relative_path} hash "
            f"{actual_manifest} != bound {stage.manifest_sha256}"
        )
    manifest = _load_json(blob, f"{stage.stage} manifest")
    declared_hash = manifest.get(stage.manifest_hash_field)
    if declared_hash != stage.manifest_hash:
        raise ReturnsBindingError(
            f"{stage.stage} {stage.manifest_hash_field} {declared_hash!r} != "
            f"bound {stage.manifest_hash!r}"
        )
    files = manifest.get("files_sha256")
    if not isinstance(files, dict):
        raise ReturnsInputError(f"{stage.stage} manifest lacks files_sha256")
    for binding in stage.files:
        declared = files.get(binding.relative_path)
        if declared != binding.sha256:
            raise ReturnsBindingError(
                f"{stage.stage} manifest binds {binding.relative_path} to "
                f"{declared!r}, expected {binding.sha256!r}"
            )
        actual = _sha256_hex(resolver.read_bytes(binding.relative_path))
        if actual != binding.sha256:
            raise ReturnsBindingError(
                f"{stage.stage} file {binding.relative_path} hash {actual} != "
                f"bound {binding.sha256}"
            )


# ---------------------------------------------------------------------------
# Input loading and reserve assertions
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Inputs:
    observations: pd.DataFrame
    ca_events: pd.DataFrame
    identity: pd.DataFrame
    day_flags: pd.DataFrame
    universe: pd.DataFrame
    formations: pd.DataFrame


def _load_inputs(resolver: Resolver, bindings: Bindings) -> _Inputs:
    observations = resolver.read_parquet(
        bindings.d3.files[0].relative_path,
        columns=["ticker", "date", "close", "raw_row_index"],
    )
    ca_events = resolver.read_parquet(
        bindings.d3.files[1].relative_path,
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
    universe = resolver.read_parquet(
        bindings.d6.files[0].relative_path,
        columns=[
            "formation_date",
            "security_key",
            "rank",
            "top_200",
            "top_500",
            "top_1000",
        ],
    )
    formations = resolver.read_parquet(
        bindings.d6.files[1].relative_path,
        columns=["formation_date", "partition", "n_eligible"],
    )
    return _Inputs(
        observations=observations,
        ca_events=ca_events,
        identity=identity,
        day_flags=day_flags,
        universe=universe,
        formations=formations,
    )


def _assert_before_reserve(series: pd.Series, what: str) -> None:
    if len(series) == 0:
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
    if len(inputs.universe):
        found = pd.to_datetime(inputs.universe["formation_date"], errors="coerce")
        if bool((found > pd.Timestamp(DEVELOPMENT_END)).any()):
            raise ReserveFirewallError(
                "universe_development contains a formation date after "
                f"{DEVELOPMENT_END}"
            )


# ---------------------------------------------------------------------------
# Population and identities
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _MemberKey:
    security_key: str
    ticker: str
    listed_start: date
    listed_end: date
    listing_status: str
    listed_end_dev: date | None


@dataclass(frozen=True)
class _MemberRow:
    formation_date: date
    security_key: str
    rank: int
    top_200: bool
    top_500: bool
    top_1000: bool


def _resolve_member_keys(
    universe: pd.DataFrame, identity: pd.DataFrame
) -> tuple[dict[str, _MemberKey], list[_MemberRow]]:
    by_key: dict[str, list[Mapping[str, Any]]] = {}
    for record in identity.to_dict("records"):
        by_key.setdefault(str(record["security_key"]), []).append(record)

    member_rows: list[_MemberRow] = []
    keys: dict[str, _MemberKey] = {}
    for record in universe.to_dict("records"):
        if not bool(record["top_1000"]):
            continue
        security_key = str(record["security_key"])
        f = _as_date(record["formation_date"])
        if f is None:
            raise D7ConsistencyError(
                f"member {security_key!r} has no formation_date"
            )
        if security_key not in keys:
            rows = by_key.get(security_key, [])
            if len(rows) != 1:
                raise D7ConsistencyError(
                    f"member security_key {security_key!r} matches "
                    f"{len(rows)} D4 identity rows (expected exactly one)"
                )
            row = rows[0]
            state = _as_str(row["state"])
            if state != STATE_INTERVAL_ASSIGNED:
                raise D7ConsistencyError(
                    f"member security_key {security_key!r} D4 state "
                    f"{state!r} != {STATE_INTERVAL_ASSIGNED}"
                )
            ticker = _as_str(row["ticker"])
            listed_start = _as_date(row["listed_start"])
            listed_end = _as_date(row["listed_end"])
            if ticker is None or listed_end is None:
                raise D7ConsistencyError(
                    f"member security_key {security_key!r} lacks ticker/listed_end"
                )
            if listed_start is None:
                raise D7ConsistencyError(
                    f"member security_key {security_key!r} has a null "
                    "listed_start; D7a fails closed rather than substituting "
                    "listed_end"
                )
            if listed_end <= DEVELOPMENT_END:
                listing_status = LISTING_ENDS_WITHIN_DEVELOPMENT
                listed_end_dev: date | None = listed_end
            else:
                listing_status = LISTING_CONTINUES_BEYOND_DEVELOPMENT_PERIOD
                listed_end_dev = None
            keys[security_key] = _MemberKey(
                security_key=security_key,
                ticker=ticker,
                listed_start=listed_start,
                listed_end=listed_end,
                listing_status=listing_status,
                listed_end_dev=listed_end_dev,
            )
        key = keys[security_key]
        if key.listed_end < f:
            raise D7ConsistencyError(
                f"member security_key {security_key!r} listed_end "
                f"{key.listed_end} < formation {f} (D6 H2 violated)"
            )
        rank = _as_int(record["rank"])
        if rank is None:
            raise D7ConsistencyError(
                f"member {security_key!r} at {f} has no rank"
            )
        member_rows.append(
            _MemberRow(
                formation_date=f,
                security_key=security_key,
                rank=rank,
                top_200=bool(record["top_200"]),
                top_500=bool(record["top_500"]),
                top_1000=bool(record["top_1000"]),
            )
        )
    return keys, member_rows


def _assert_member_counts(
    member_rows: Sequence[_MemberRow], formations: pd.DataFrame
) -> None:
    n_eligible: dict[date, int] = {}
    for record in formations.to_dict("records"):
        if _as_str(record["partition"]) != PARTITION_DEVELOPMENT:
            continue
        f = _as_date(record["formation_date"])
        n = _as_int(record["n_eligible"])
        if f is not None:
            n_eligible[f] = 0 if n is None else n
    counts: dict[date, int] = {}
    ranks: dict[date, list[int]] = {}
    for row in member_rows:
        counts[row.formation_date] = counts.get(row.formation_date, 0) + 1
        ranks.setdefault(row.formation_date, []).append(row.rank)
    for f in set(counts) | set(n_eligible):
        expected = min(1000, n_eligible.get(f, 0))
        actual = counts.get(f, 0)
        if actual != expected:
            raise D7ConsistencyError(
                f"formation {f} has {actual} top_1000 members, expected "
                f"min(1000, n_eligible={n_eligible.get(f, 0)}) = {expected}"
            )
        ordered = sorted(ranks.get(f, []))
        if ordered != list(range(1, actual + 1)):
            raise D7ConsistencyError(
                f"formation {f} ranks are not 1..{actual}: {ordered!r}"
            )


def _assert_formation_equality(
    universe: pd.DataFrame, formations: pd.DataFrame
) -> None:
    universe_dates = {
        _as_date(value) for value in universe["formation_date"].tolist()
    }
    universe_dates.discard(None)
    development = {
        _as_date(record["formation_date"])
        for record in formations.to_dict("records")
        if _as_str(record["partition"]) == PARTITION_DEVELOPMENT
    }
    development.discard(None)
    if universe_dates != development:
        raise D7ConsistencyError(
            "universe_development formation dates do not equal the "
            f"formations.parquet DEVELOPMENT rows "
            f"(missing={sorted(development - universe_dates)}, "
            f"extra={sorted(universe_dates - development)})"
        )


# ---------------------------------------------------------------------------
# Market-row and corporate-action indexes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Row:
    raw_row_index: int
    close: float


@dataclass(frozen=True)
class _CaEvent:
    effective_date: date | None
    action_type: str | None
    knowledge_date: date | None
    adjustment_factor: float | None
    is_superseded: bool


def _index_rows(
    observations: pd.DataFrame, day_flags: pd.DataFrame
) -> tuple[
    dict[str, dict[date, list[_Row]]],
    set[tuple[str, date, int]],
    set[tuple[str, date, int]],
]:
    unusable: set[tuple[str, date, int]] = set()
    suspect: set[tuple[str, date, int]] = set()
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
        if klass == CLASS_CORP_ACTION_SUSPECT:
            suspect.add((ticker, d, idx))

    rows: dict[str, dict[date, list[_Row]]] = {}
    for record in observations.to_dict("records"):
        ticker = _as_str(record["ticker"])
        d = _as_date(record["date"])
        idx = _as_int(record["raw_row_index"])
        if ticker is None or d is None or idx is None:
            continue
        close = _as_float(record["close"])
        rows.setdefault(ticker, {}).setdefault(d, []).append(
            _Row(raw_row_index=idx, close=0.0 if close is None else close)
        )
    return rows, unusable, suspect


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
            raise D7ConsistencyError(
                f"corporate action dated {event.effective_date} is superseded"
            )
        if event.knowledge_date != event.effective_date:
            raise D7ConsistencyError(
                f"corporate action dated {event.effective_date} has "
                f"knowledge_date {event.knowledge_date}"
            )
        if (
            event.adjustment_factor is None
            or not math.isfinite(event.adjustment_factor)
            or event.adjustment_factor <= 0
        ):
            raise D7ConsistencyError(
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
            raise D7ConsistencyError(
                f"corporate action dated {event.effective_date} is not a "
                "trusted session (unmatched fails closed)"
            )


# ---------------------------------------------------------------------------
# Per-key daily evidence
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _DailyRecord:
    security_key: str
    date: date
    prev_session: date
    session_status: str
    daily_total_return: float | None
    ca_event_count: int
    corp_action_suspect: bool


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


def _key_evidence_indices(
    formations: Sequence[date],
    pos: Mapping[date, int],
    dev_end_idx: int,
) -> list[int]:
    marked: set[int] = set()
    for f in formations:
        lo = pos[f] + 1
        hi = min(pos[f] + HMAX, dev_end_idx)
        if lo <= hi:
            marked.update(range(lo, hi + 1))
    return sorted(marked)


def _compute_daily(
    key: _MemberKey,
    evidence_idx: Sequence[int],
    sessions: Sequence[date],
    rows_by_ticker: Mapping[str, Mapping[date, list[_Row]]],
    unusable: set[tuple[str, date, int]],
    suspect: set[tuple[str, date, int]],
    ca_events: Sequence[_CaEvent],
) -> tuple[list[_DailyRecord], dict[int, str]]:
    event_counts: dict[date, int] = {}
    for event in ca_events:
        assert event.effective_date is not None
        event_counts[event.effective_date] = (
            event_counts.get(event.effective_date, 0) + 1
        )

    ticker_rows = rows_by_ticker.get(key.ticker, {})
    preliminary: list[tuple[int, date, date, str, float | None, int, bool]] = []
    status_by_idx: dict[int, str] = {}
    valid_records: list[tuple[date, float]] = []

    for si in evidence_idx:
        s = sessions[si]
        p = sessions[si - 1]
        flag = False
        raw: float | None = None
        if key.listed_end <= DEVELOPMENT_END and s > key.listed_end:
            status = STATUS_POST_TERMINAL
        else:
            rows_s = ticker_rows.get(s, [])
            if not rows_s:
                status = STATUS_NO_ROW
            else:
                usable_s = _usable_rows(rows_s, key.ticker, s, unusable)
                if not usable_s:
                    status = STATUS_ROW_UNUSABLE
                elif len(usable_s) > 1:
                    raise D7ConsistencyError(
                        f"{key.security_key!r} has {len(usable_s)} usable rows "
                        f"on session {s}"
                    )
                else:
                    flag = (key.ticker, s, usable_s[0].raw_row_index) in suspect
                    rows_p = ticker_rows.get(p, [])
                    if not rows_p:
                        status = STATUS_PREV_NO_ROW
                    else:
                        usable_p = _usable_rows(rows_p, key.ticker, p, unusable)
                        if not usable_p:
                            status = STATUS_PREV_ROW_UNUSABLE
                        elif len(usable_p) > 1:
                            raise D7ConsistencyError(
                                f"{key.security_key!r} has {len(usable_p)} "
                                f"usable rows on session {p}"
                            )
                        else:
                            status = STATUS_VALID
                            raw = usable_s[0].close / usable_p[0].close - 1.0
                            valid_records.append((s, raw))
        preliminary.append(
            (si, s, p, status, raw, event_counts.get(s, 0), flag)
        )
        status_by_idx[si] = status

    adjusted = _adjusted_returns(key, valid_records, ca_events)
    records: list[_DailyRecord] = []
    for si, s, p, status, _raw, count, flag in preliminary:
        value: float | None = None
        if status == STATUS_VALID:
            value = adjusted.get(s)
            if value is None:
                raise D7ConsistencyError(
                    f"no adjusted return produced for {key.security_key!r} {s}"
                )
            if not math.isfinite(value) or value <= -1.0:
                raise D7ConsistencyError(
                    f"daily_total_return {value!r} for {key.security_key!r} "
                    f"on {s} is not finite and > -1"
                )
        records.append(
            _DailyRecord(
                security_key=key.security_key,
                date=s,
                prev_session=p,
                session_status=status,
                daily_total_return=value,
                ca_event_count=count,
                corp_action_suspect=flag,
            )
        )
    return records, status_by_idx


def _adjusted_returns(
    key: _MemberKey,
    valid_records: Sequence[tuple[date, float]],
    ca_events: Sequence[_CaEvent],
) -> dict[date, float]:
    if not valid_records:
        return {}
    raw_panel = pd.DataFrame(
        {
            "date": pd.to_datetime([record[0] for record in valid_records]),
            "stock_id": pd.Series(
                [key.security_key] * len(valid_records), dtype="string"
            ),
            "raw_ret": np.array(
                [record[1] for record in valid_records], dtype=np.float64
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
# Labels
# ---------------------------------------------------------------------------


def _label_records(
    f: date,
    key: _MemberKey,
    status_by_idx: Mapping[int, str],
    sessions: Sequence[date],
    pos: Mapping[date, int],
) -> list[list[Any]]:
    pos_f = pos[f]
    lid = bisect.bisect_right(sessions, key.listed_end) - 1
    l_offset = max(0, lid - pos_f)
    within_development = key.listed_end <= DEVELOPMENT_END

    statuses: list[str | None] = [
        status_by_idx.get(pos_f + h) for h in range(1, HMAX + 1)
    ]
    prefix = [0] * (HMAX + 1)
    first_invalid_offset: list[int | None] = [None] * (HMAX + 1)
    for k in range(HMAX):
        status = statuses[k]
        invalid = status is not None and status != STATUS_VALID
        prefix[k + 1] = prefix[k] + (1 if invalid else 0)
        if invalid:
            first_invalid_offset[k + 1] = (
                first_invalid_offset[k]
                if first_invalid_offset[k] is not None
                else k + 1
            )
        else:
            first_invalid_offset[k + 1] = first_invalid_offset[k]

    records: list[list[Any]] = []
    for h in range(1, HMAX + 1):
        e = sessions[pos_f + h]
        if e >= RESERVE_START:
            records.append(
                [
                    f,
                    key.security_key,
                    h,
                    e,
                    STATE_PURGED_RESERVE_CROSSING,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                ]
            )
            continue
        terminal_affected = within_development and key.listed_end <= e
        n_obs = min(h, l_offset) if terminal_affected else h
        # Fail closed: for a non-crossing label every session in the observed
        # segment must have evidence. ``None`` is not silently VALID.
        for k in range(n_obs):
            if statuses[k] is None:
                raise D7ConsistencyError(
                    f"missing session evidence for {key.security_key!r} at "
                    f"{sessions[pos_f + k + 1]} (label h={h})"
                )
        n_post = h - n_obs
        n_invalid = prefix[n_obs]
        if n_invalid > 0:
            offset = first_invalid_offset[n_obs]
            assert offset is not None
            first_session: date | None = sessions[pos_f + offset]
            first_status: str | None = statuses[offset - 1]
        else:
            first_session = None
            first_status = None
        observed_end = sessions[pos_f + n_obs] if n_obs > 0 else None
        complete = n_invalid == 0
        if terminal_affected and n_obs == 0:
            state = STATE_EMPTY_OBSERVED_PATH_IDENTITY
        elif complete:
            state = STATE_COMPLETE
        else:
            state = STATE_INCOMPLETE
        records.append(
            [
                f,
                key.security_key,
                h,
                e,
                state,
                terminal_affected,
                complete,
                n_obs,
                n_post,
                n_invalid,
                observed_end,
                first_session,
                first_status,
            ]
        )
    return records


# ---------------------------------------------------------------------------
# Output construction
# ---------------------------------------------------------------------------


def _compute_returns_id(
    bindings: Bindings, documents: Mapping[str, str], code_sha: str
) -> str:
    payload = {
        "bindings": {
            stage.stage: {
                "artifact_id": stage.artifact_id,
                "hash": stage.manifest_hash,
                "manifest_sha256": stage.manifest_sha256,
                "files_sha256": {
                    binding.relative_path: binding.sha256
                    for binding in stage.files
                },
            }
            for stage in bindings.stages()
        },
        "documents": dict(documents),
        "returns_code_sha256": code_sha,
    }
    digest = _sha256_hex(canonical_json(payload).encode("utf-8"))
    return "r-" + digest[:16]


def _bindings_summary(bindings: Bindings) -> dict[str, Any]:
    return {
        stage.stage: {
            "artifact_id": stage.artifact_id,
            "manifest_path": stage.manifest_relative_path,
            "manifest_hash_field": stage.manifest_hash_field,
            "hash": stage.manifest_hash,
            "manifest_sha256": stage.manifest_sha256,
            "files_sha256": {
                binding.relative_path: binding.sha256 for binding in stage.files
            },
        }
        for stage in bindings.stages()
    }


def _build_members(
    member_rows: Sequence[_MemberRow], keys: Mapping[str, _MemberKey]
) -> pd.DataFrame:
    records: list[list[Any]] = []
    for row in member_rows:
        key = keys[row.security_key]
        records.append(
            [
                row.formation_date,
                row.security_key,
                key.ticker,
                row.rank,
                row.top_200,
                row.top_500,
                row.top_1000,
                key.listed_end_dev,
                key.listing_status,
                TERMINAL_EVIDENCE_SOURCE,
            ]
        )
    frame = _build_frame(records, _MEMBERS_COLUMNS, _MEMBERS_DTYPES)
    if not frame.empty:
        frame = frame.sort_values(
            ["formation_date", "rank"], kind="mergesort", ignore_index=True
        )
    return frame


def _build_horizons(
    formations: Sequence[date],
    sessions: Sequence[date],
    pos: Mapping[date, int],
) -> pd.DataFrame:
    records: list[list[Any]] = []
    for f in formations:
        for h in range(1, HMAX + 1):
            endpoint = sessions[pos[f] + h]
            records.append([f, h, endpoint, endpoint >= RESERVE_START])
    frame = _build_frame(records, _HORIZONS_COLUMNS, _HORIZONS_DTYPES)
    if not frame.empty:
        frame = frame.sort_values(
            ["formation_date", "h"], kind="mergesort", ignore_index=True
        )
    return frame


def _build_daily(records: Sequence[_DailyRecord]) -> pd.DataFrame:
    rows: list[list[Any]] = [
        [
            record.security_key,
            record.date,
            record.prev_session,
            record.session_status,
            record.daily_total_return,
            record.ca_event_count,
            record.corp_action_suspect,
        ]
        for record in records
    ]
    frame = _build_frame(rows, _DAILY_COLUMNS, _DAILY_DTYPES)
    if not frame.empty:
        frame = frame.sort_values(
            ["security_key", "date"], kind="mergesort", ignore_index=True
        )
    return frame


def _build_labels(records: Sequence[Sequence[Any]]) -> pd.DataFrame:
    frame = _build_frame(records, _LABELS_COLUMNS, _LABELS_DTYPES)
    if not frame.empty:
        frame = frame.sort_values(
            ["formation_date", "security_key", "h"],
            kind="mergesort",
            ignore_index=True,
        )
    return frame


def _assert_output_dates(
    members: pd.DataFrame,
    horizons: pd.DataFrame,
    labels: pd.DataFrame,
    daily: pd.DataFrame,
) -> None:
    for column, frame in (
        ("date", daily),
        ("prev_session", daily),
        ("listed_end_dev", members),
        ("observed_segment_end", labels),
        ("first_invalid_session", labels),
    ):
        if column not in frame:
            continue
        values = frame[column]
        if len(values) == 0:
            continue
        parsed = pd.to_datetime(values, errors="coerce")
        if bool((parsed >= pd.Timestamp(RESERVE_START)).any()):
            raise ReserveFirewallError(f"{column} holds a reserve date")
    if len(horizons):
        parsed_endpoints = pd.to_datetime(horizons["endpoint"], errors="coerce")
        over = parsed_endpoints >= pd.Timestamp(RESERVE_START)
        if not bool((over == horizons["crosses_reserve"]).all()):
            raise ReserveFirewallError(
                "horizons.endpoint / crosses_reserve disagree"
            )


def _counts(series: pd.Series) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in series.tolist():
        if value is None:
            continue
        text = str(value)
        counts[text] = counts.get(text, 0) + 1
    return dict(sorted(counts.items()))


def _write_outputs(
    root: Path,
    returns_id: str,
    members: pd.DataFrame,
    horizons: pd.DataFrame,
    daily: pd.DataFrame,
    labels: pd.DataFrame,
    content: dict[str, Any],
) -> dict[str, Any]:
    derived = root / "derived" / "returns"
    derived.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=returns_id + ".", dir=derived))
    try:
        members_path = stage / MEMBERS_FILE
        horizons_path = stage / HORIZONS_FILE
        daily_path = stage / DAILY_FILE
        labels_path = stage / LABELS_FILE
        for frame, path in (
            (members, members_path),
            (horizons, horizons_path),
            (daily, daily_path),
            (labels, labels_path),
        ):
            frame.to_parquet(
                path, engine="pyarrow", index=False, compression="zstd"
            )
        files_sha256 = {
            f"derived/returns/{returns_id}/{MEMBERS_FILE}": _sha256_file(
                members_path
            ),
            f"derived/returns/{returns_id}/{HORIZONS_FILE}": _sha256_file(
                horizons_path
            ),
            f"derived/returns/{returns_id}/{DAILY_FILE}": _sha256_file(daily_path),
            f"derived/returns/{returns_id}/{LABELS_FILE}": _sha256_file(
                labels_path
            ),
        }
        content["files_sha256"] = files_sha256
        content["returns_hash"] = _sha256_hex(
            canonical_json(files_sha256).encode("utf-8")
        )
        (stage / REPORT_FILE).write_bytes(_canonical_bytes(content))

        target = derived / returns_id
        if target.exists():
            existing = sorted(p.name for p in target.iterdir())
            produced = sorted(p.name for p in stage.iterdir())
            if existing != produced or any(
                (target / name).read_bytes() != (stage / name).read_bytes()
                for name in produced
            ):
                raise NondeterministicReturnsError(
                    f"existing {target} holds different bytes for {returns_id}"
                )
            shutil.rmtree(stage)
        else:
            stage.rename(target)
        return content
    finally:
        shutil.rmtree(stage, ignore_errors=True)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def run_returns(
    root: Path | str,
    *,
    bindings: Bindings | None = None,
    calendar: Any | None = None,
) -> dict[str, Any]:
    """Build the D7a development observed-return evidence and return its JSON."""
    bindings = bindings if bindings is not None else DEFAULT_BINDINGS
    if not isinstance(bindings, Bindings):
        raise ReturnsInputError("bindings must be a Bindings value")
    calendar = (
        calendar
        if calendar is not None
        else build_nyse_calendar(CALENDAR_START, CALENDAR_END)
    )
    root = Path(root)
    resolver = Resolver(root, bindings)
    documents = _verify_documents(bindings)
    code_sha = _sha256_hex(Path(__file__).read_bytes())
    returns_id = _compute_returns_id(bindings, documents, code_sha)

    for stage in bindings.stages():
        _verify_stage(resolver, stage)

    inputs = _load_inputs(resolver, bindings)
    _post_load_assertions(inputs)
    _assert_formation_equality(inputs.universe, inputs.formations)

    sessions = [ts.date() for ts in calendar.dates]
    pos = {d: i for i, d in enumerate(sessions)}
    dev_end_idx = pos[DEVELOPMENT_END]

    keys, member_rows = _resolve_member_keys(inputs.universe, inputs.identity)
    _assert_member_counts(member_rows, inputs.formations)

    rows_by_ticker, unusable, suspect = _index_rows(
        inputs.observations, inputs.day_flags
    )
    ca_by_ticker = _index_ca_events(inputs.ca_events)
    session_set = frozenset(sessions)

    formations_by_key: dict[str, list[date]] = {}
    for row in member_rows:
        formations_by_key.setdefault(row.security_key, []).append(
            row.formation_date
        )

    daily_records: list[_DailyRecord] = []
    status_maps: dict[str, dict[int, str]] = {}
    for security_key, formations in formations_by_key.items():
        key = keys[security_key]
        upper = min(key.listed_end, DEVELOPMENT_END)
        events = _member_ca_events(
            ca_by_ticker.get(key.ticker, []), key.listed_start, upper
        )
        _validate_ca_sessions(events, session_set)
        evidence_idx = _key_evidence_indices(formations, pos, dev_end_idx)
        records, status_by_idx = _compute_daily(
            key,
            evidence_idx,
            sessions,
            rows_by_ticker,
            unusable,
            suspect,
            events,
        )
        daily_records.extend(records)
        status_maps[security_key] = status_by_idx

    label_records: list[list[Any]] = []
    for row in member_rows:
        label_records.extend(
            _label_records(
                row.formation_date,
                keys[row.security_key],
                status_maps[row.security_key],
                sessions,
                pos,
            )
        )

    formations = sorted({row.formation_date for row in member_rows})
    members = _build_members(member_rows, keys)
    horizons = _build_horizons(formations, sessions, pos)
    daily = _build_daily(daily_records)
    labels = _build_labels(label_records)

    _assert_output_dates(members, horizons, labels, daily)
    purged = int(
        (labels["label_state"] == STATE_PURGED_RESERVE_CROSSING).sum()
    )
    terminal_affected_per_h: dict[str, int] = {}
    for h in range(1, HMAX + 1):
        subset = labels[
            (labels["h"] == h) & labels["terminal_affected"].fillna(False)
        ]
        terminal_affected_per_h[str(h)] = int(len(subset))

    content: dict[str, Any] = {
        "schema": SCHEMA,
        "returns_id": returns_id,
        "returns_code_sha256": code_sha,
        "bindings": _bindings_summary(bindings),
        "documents": dict(documents),
        "constants": {
            "as_of": AS_OF.isoformat(),
            "pit_availability_buffer_days": PIT_BUFFER_DAYS,
            "hmax": HMAX,
            "reserve_start": RESERVE_START.isoformat(),
            "development_end": DEVELOPMENT_END.isoformat(),
        },
        "counts": {
            "session_status": _counts(daily["session_status"]),
            "label_state": _counts(labels["label_state"]),
        },
        "terminal_affected_count_per_h": terminal_affected_per_h,
        "purge_count": purged,
        "member_count": int(len(members)),
        "d7b_obligations": list(D7B_OBLIGATIONS),
        "certification_boundary": CERTIFICATION_BOUNDARY,
    }
    return _write_outputs(
        root, returns_id, members, horizons, daily, labels, content
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build the D7a development observed-return evidence."
    )
    parser.add_argument("--root", required=True, help="data_store root")
    args = parser.parse_args(argv)
    content = run_returns(args.root)
    print(content["returns_id"])
    print(content["returns_hash"])
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
