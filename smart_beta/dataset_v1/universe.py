"""Discovery Dataset v1.1 — D6 development PIT universe construction.

D6 builds the point-in-time (PIT) eligible universe for the **90 development
formations only** (2016-01-29 … 2023-06-30).  Reserve formations are
``DEFERRED`` and are never ranked, materialised or inspected (D6 clarification
H1).  The module is deliberately offline and value-free at the reserve
boundary: it reads one D3 development observation partition, one D4 identity
assignment, one D5 development day-flag partition and the two D-SC timelines,
joins them, applies the frozen eligibility checks and writes the development
universe, the per-formation schedule and the development audit.

Governing authority (frozen), in precedence order: the Dataset-v1 contract
§§3, 4, 5, 6, 9, 10, 11, 12, 16 (gate 11); the v1.1 classification amendment;
the v1.1 identity-binding clarification; the v1.1 D6 clarification; the
Dataset-v1.1 decisions §2 (Q-D6-1); and the D5 / D-SC task interfaces.

Reserve firewall (mechanical, fail closed): every data file is opened through
one :class:`Resolver`, which refuses any path whose components include
``reserve_sealed``, any path not derived from the frozen :class:`Bindings`,
and any requested formation date on or after 2023-07-03.  After loading, every
observation and day-flag date must be earlier than 2023-07-03.  D-SC queries
use cutoffs on or before 2023-06-29 only.  No output carries a reserve-derived
price, volume, dollar volume, rank, membership or cross-sectional statistic.

The module never makes a provider or network call, never reads a credential
value, never reads the real ``data_store`` (tests inject synthetic bindings and
``tmp_path`` fixtures), never re-derives classification and never reads an
adjusted price / volume field.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import tempfile
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd
import pyarrow as pa

from smart_beta.dataset_v1.classification import class_state_at, policy_state_at
from smart_beta.vendors.tiingo.calendar_source import build_nyse_calendar

__all__ = [
    "SCHEMA",
    "RESERVE_START",
    "DEVELOPMENT_END",
    "ACQUISITION_START",
    "CALENDAR_START",
    "CALENDAR_END",
    "SCHEDULE_START",
    "SCHEDULE_END",
    "DSC_MAX_CUTOFF",
    "N_FULL",
    "N_DEVELOPMENT",
    "N_RESERVE",
    "LIQUIDITY_WINDOW",
    "MIN_LIQUIDITY_SESSIONS",
    "MIN_LISTING_AGE_SESSIONS",
    "MIN_ELIGIBLE",
    "ELIGIBLE_EXCHANGES",
    "REASON_ELIGIBLE",
    "REASON_IDENTITY_NOT_ADMISSIBLE",
    "REASON_EXCHANGE_NOT_ELIGIBLE",
    "REASON_NOT_COMMON_STOCK",
    "REASON_ISSUER_POLICY_EXCLUDED",
    "REASON_ISSUER_POLICY_UNRESOLVED",
    "REASON_LISTING_AGE_INSUFFICIENT",
    "REASON_PIT_MISSINGNESS_EMPTY",
    "REASON_PIT_MISSINGNESS_EXCEEDED",
    "REASON_TRAILING_LIQUIDITY_INSUFFICIENT",
    "STATUS_OK",
    "STATUS_UNIVERSE_SHORTFALL",
    "STATUS_DEFERRED",
    "PARTITION_DEVELOPMENT",
    "PARTITION_RESERVE",
    "FileBinding",
    "StageBinding",
    "DocumentBinding",
    "Bindings",
    "Schedule",
    "Resolver",
    "D6Error",
    "UniverseInputError",
    "UniverseBindingError",
    "ReserveFirewallError",
    "D6ConsistencyError",
    "NondeterministicUniverseError",
    "canonical_json",
    "default_bindings",
    "build_schedule",
    "run_universe",
    "main",
]


# ---------------------------------------------------------------------------
# Frozen constants
# ---------------------------------------------------------------------------

SCHEMA = "discovery-dataset-v1-universe-v1"

#: Fixed start of the sealed historical reserve (contract §§11.2, 12).
RESERVE_START = date(2023, 7, 3)
#: Last development formation.
DEVELOPMENT_END = date(2023, 6, 30)
#: Acquisition start (contract §2).
ACQUISITION_START = date(2015, 1, 2)
#: Trusted-calendar bounds.
CALENDAR_START = date(2015, 1, 2)
CALENDAR_END = date(2027, 12, 31)
#: Full-schedule month-end generation bounds (task spec §4).
SCHEDULE_START = date(2016, 1, 4)
SCHEDULE_END = date(2026, 8, 31)
#: The latest cutoff a D-SC PIT query may use (D6 clarification H1).
DSC_MAX_CUTOFF = date(2023, 6, 29)

N_FULL = 128
N_DEVELOPMENT = 90
N_RESERVE = 38

#: Liquidity window W_f is the 63 trusted NYSE sessions f-63 … f-1.
LIQUIDITY_WINDOW = 63
#: Rule 9: at least 50 liquidity-valid sessions in W_f.
MIN_LIQUIDITY_SESSIONS = 50
#: Rule 6: listing age of at least 252 trusted sessions.
MIN_LISTING_AGE_SESSIONS = 252
#: Rule threshold: a formation with fewer eligible keys fails gate 11.
MIN_ELIGIBLE = 500

#: D5 dispositions / classes D6 must fan out on.
DISPOSITION_CORRUPT = "CORRUPT"
CLASS_INTERVAL_MISMATCH = "INTERVAL_MISMATCH"
CLASS_VENDOR_NULL = "VENDOR_NULL"
CLASS_CORP_ACTION_SUSPECT = "CORP_ACTION_SUSPECT"

#: D4 identity state admissible for the universe.
STATE_INTERVAL_ASSIGNED = "INTERVAL_ASSIGNED"
#: D-SC class / policy vocabulary D6 consumes (never re-derived).
CLASS_COMMON_STOCK = "COMMON_STOCK"
POLICY_EXCLUDED = "EXCLUDED"
POLICY_UNRESOLVED = "ISSUER_STATUS_UNRESOLVED"
POLICY_CLEAR = "NO_REGULATORY_EXCLUSION_OBSERVED"

#: §3.1 eligible venues.
ELIGIBLE_EXCHANGES = frozenset({"NYSE", "NASDAQ", "NYSE MKT", "AMEX"})

#: Eligibility reason codes (one primary reason, first failing check).
REASON_ELIGIBLE = "ELIGIBLE"
REASON_IDENTITY_NOT_ADMISSIBLE = "IDENTITY_NOT_ADMISSIBLE"
REASON_EXCHANGE_NOT_ELIGIBLE = "EXCHANGE_NOT_ELIGIBLE"
REASON_NOT_COMMON_STOCK = "NOT_COMMON_STOCK"
REASON_ISSUER_POLICY_EXCLUDED = "ISSUER_POLICY_EXCLUDED"
REASON_ISSUER_POLICY_UNRESOLVED = "ISSUER_POLICY_UNRESOLVED"
REASON_LISTING_AGE_INSUFFICIENT = "LISTING_AGE_INSUFFICIENT"
REASON_PIT_MISSINGNESS_EMPTY = "PIT_MISSINGNESS_EMPTY"
REASON_PIT_MISSINGNESS_EXCEEDED = "PIT_MISSINGNESS_EXCEEDED"
REASON_TRAILING_LIQUIDITY_INSUFFICIENT = "TRAILING_LIQUIDITY_INSUFFICIENT"

STATUS_OK = "OK"
STATUS_UNIVERSE_SHORTFALL = "UNIVERSE_SHORTFALL"
STATUS_DEFERRED = "DEFERRED"

PARTITION_DEVELOPMENT = "DEVELOPMENT"
PARTITION_RESERVE = "RESERVE"

#: Certification boundary text written into ``universe.json`` (task spec §10).
CERTIFICATION_BOUNDARY = (
    "D6 certifies only development PIT-universe construction under the frozen "
    "Dataset-v1 assumptions. Reserve D6 and reserve Gate 11: DEFERRED / NOT "
    "ASSESSED. Issuer-identity knowledge timing/continuity: NOT CERTIFIED. "
    "Historical delisting-knowledge timing is not universally PIT-certified. "
    "Not claimed: alpha or factor validity; production investability; "
    "delisting returns; transaction-cost realism; capacity or borrow "
    "availability; complete survivorship elimination; reserve access or "
    "confirmation consumption."
)

_STRING = pd.ArrowDtype(pa.string())
_DATE32 = pd.ArrowDtype(pa.date32())
_INT64 = pd.ArrowDtype(pa.int64())
_FLOAT64 = pd.ArrowDtype(pa.float64())
_BOOL = pd.ArrowDtype(pa.bool_())

#: Relative output filenames under ``derived/universe/<universe_id>/``.
DEVELOPMENT_FILE = "universe_development.parquet"
FORMATIONS_FILE = "formations.parquet"
AUDIT_FILE = "audit_development.parquet"
REPORT_FILE = "universe.json"

_UNIVERSE_DEV_COLUMNS = (
    "formation_date",
    "security_key",
    "rank",
    "top_200",
    "top_500",
    "top_1000",
)
_UNIVERSE_DEV_DTYPES: dict[str, Any] = {
    "formation_date": _DATE32,
    "security_key": _STRING,
    "rank": _INT64,
    "top_200": _BOOL,
    "top_500": _BOOL,
    "top_1000": _BOOL,
}

_FORMATIONS_COLUMNS = (
    "formation_date",
    "cutoff_date",
    "partition",
    "n_population",
    "n_eligible",
    "status",
    "top_1000_shortfall",
)
_FORMATIONS_DTYPES: dict[str, Any] = {
    "formation_date": _DATE32,
    "cutoff_date": _DATE32,
    "partition": _STRING,
    "n_population": _INT64,
    "n_eligible": _INT64,
    "status": _STRING,
    "top_1000_shortfall": _BOOL,
}

_AUDIT_COLUMNS = (
    "formation_date",
    "security_key",
    "ticker",
    "identity_state",
    "class_state",
    "class_reason",
    "policy_state",
    "listing_age_sessions",
    "missing_numerator",
    "missing_denominator",
    "trailing_valid_sessions",
    "median_dv",
    "reason",
    "rank",
)
_AUDIT_DTYPES: dict[str, Any] = {
    "formation_date": _DATE32,
    "security_key": _STRING,
    "ticker": _STRING,
    "identity_state": _STRING,
    "class_state": _STRING,
    "class_reason": _STRING,
    "policy_state": _STRING,
    "listing_age_sessions": _INT64,
    "missing_numerator": _INT64,
    "missing_denominator": _INT64,
    "trailing_valid_sessions": _INT64,
    "median_dv": _FLOAT64,
    "reason": _STRING,
    "rank": _INT64,
}

_DOCUMENT_NAMES = frozenset(
    {
        "contract",
        "classification_amendment",
        "identity_binding_clarification",
        "d6_clarification",
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
    files: tuple[FileBinding, ...]


@dataclass(frozen=True)
class DocumentBinding:
    """One normative document that D6's authority list hash-binds."""

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
    documents: tuple[DocumentBinding, ...]

    def stages(self) -> tuple[StageBinding, StageBinding, StageBinding, StageBinding]:
        return (self.d3, self.d4, self.d5, self.dsc)

    def allowlist(self) -> frozenset[str]:
        """The only paths D6 may open, relative to ``<root>``."""
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
            files=(
                FileBinding(
                    "normalized/n-f874d3ecb61374d4/observations.parquet",
                    "557001d0e9c617befd3c036683f171094e86e19badb760098f40c08f3f56596e",
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
            manifest_relative_path="derived/classification/c-27b6dabd0db0cb15/dsc.json",
            manifest_hash_field="dsc_hash",
            manifest_hash=(
                "2ee4940c14a85048fddd9e3e675c3e9a6f40a11c8887192a5e61939adfc62b60"
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
        documents=(
            DocumentBinding(
                "contract",
                str(_REPO_ROOT / "docs" / "discovery_dataset_v1_contract.md"),
                "d03f35549777e5a94046f06dd0621acfc3a1dedb7a4b40adc55fdae911fa80e3",
            ),
            DocumentBinding(
                "classification_amendment",
                str(
                    _REPO_ROOT
                    / "docs"
                    / "discovery_dataset_v1_1_classification_amendment.md"
                ),
                "f89a7128bb8113c6978fc6bca8bcaf6048cd0c7db1fe5ac2aaffa1c7d519c623",
            ),
            DocumentBinding(
                "identity_binding_clarification",
                str(
                    _REPO_ROOT
                    / "docs"
                    / "discovery_dataset_v1_1_identity_binding_clarification.md"
                ),
                "57da1603ff0c77f19460293802d34f2b2383800e0528746dcb8fbb6b9ceedfa9",
            ),
            DocumentBinding(
                "d6_clarification",
                str(
                    _REPO_ROOT / "docs" / "discovery_dataset_v1_1_d6_clarification.md"
                ),
                "e3cb52a892f533c41c5c0d1d927cd54d7e200ef2a28e56e76efdf01227aa17ea",
            ),
        ),
    )


DEFAULT_BINDINGS = default_bindings()


# ---------------------------------------------------------------------------
# Typed errors (fail closed)
# ---------------------------------------------------------------------------


class D6Error(Exception):
    """Base class for every D6 fail-closed error."""


class UniverseInputError(D6Error, ValueError):
    """A required input is missing, unreadable or malformed."""


class UniverseBindingError(D6Error, ValueError):
    """A binding hash does not match (fail closed, no output written)."""


class ReserveFirewallError(D6Error, ValueError):
    """A read on or beyond the reserve boundary was attempted."""


class D6ConsistencyError(UniverseInputError):
    """The inputs are internally inconsistent in a way D6 cannot resolve."""


class NondeterministicUniverseError(D6Error):
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
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, pd.Timestamp):
        return value.date()
    raise UniverseInputError(f"unexpected date value {value!r}")


def _as_int(value: Any) -> int | None:
    if value is None or value is pd.NaT:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return int(value)


def _float_or_none(value: Any) -> float | None:
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
    """The single guarded gateway for every D6 data-file open."""

    def __init__(self, root: Path | str, bindings: Bindings):
        self._root = Path(root)
        self._bindings = bindings
        self._allow = bindings.allowlist()

    @property
    def allowlist(self) -> frozenset[str]:
        return self._allow

    def resolve(
        self,
        relative_path: str,
        *,
        formation_date: date | None = None,
    ) -> Path:
        """Resolve one allowlisted path, refusing anything across the firewall.

        Raises :class:`ReserveFirewallError` if any path component is
        ``reserve_sealed``, if ``relative_path`` is not derived from the
        bindings, or if ``formation_date`` is on or after 2023-07-03.
        """
        raw = str(relative_path)
        pure = PurePosixPath(raw)
        if pure.is_absolute() or ".." in pure.parts:
            raise UniverseInputError(f"path {raw!r} escapes <root>")
        if any(part == "reserve_sealed" for part in pure.parts):
            raise ReserveFirewallError(
                f"refusing reserve path component in {raw!r}"
            )
        if formation_date is not None and formation_date >= RESERVE_START:
            raise ReserveFirewallError(
                f"refusing formation date {formation_date} on/after {RESERVE_START}"
            )
        if raw not in self._allow:
            raise ReserveFirewallError(f"path {raw!r} is not allowlisted")
        return self._root / pure

    def read_bytes(
        self,
        relative_path: str,
        *,
        formation_date: date | None = None,
    ) -> bytes:
        path = self.resolve(relative_path, formation_date=formation_date)
        try:
            return path.read_bytes()
        except OSError as exc:
            raise UniverseInputError(f"cannot read {path}: {exc}") from exc


def _load_json(blob: bytes, what: str) -> dict:
    try:
        obj = json.loads(blob)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UniverseInputError(f"{what} is not valid JSON: {exc}") from exc
    if not isinstance(obj, dict):
        raise UniverseInputError(f"{what} is not a JSON object")
    return obj


def _verify_documents(bindings: Bindings) -> dict[str, str]:
    names = {doc.name for doc in bindings.documents}
    if names != _DOCUMENT_NAMES:
        raise UniverseInputError(
            f"document bindings must be exactly {sorted(_DOCUMENT_NAMES)}, "
            f"got {sorted(names)}"
        )
    hashes: dict[str, str] = {}
    for doc in bindings.documents:
        try:
            blob = Path(doc.path).read_bytes()
        except OSError as exc:
            raise UniverseInputError(
                f"cannot read document {doc.name} at {doc.path}: {exc}"
            ) from exc
        actual = _sha256_hex(blob)
        if actual != doc.sha256:
            raise UniverseBindingError(
                f"document {doc.name} hash {actual} != bound {doc.sha256}"
            )
        hashes[doc.name] = actual
    return hashes


def _verify_stage(resolver: Resolver, stage: StageBinding) -> dict:
    manifest = _load_json(
        resolver.read_bytes(stage.manifest_relative_path),
        f"{stage.stage} manifest",
    )
    declared_hash = manifest.get(stage.manifest_hash_field)
    if declared_hash != stage.manifest_hash:
        raise UniverseBindingError(
            f"{stage.stage} {stage.manifest_hash_field} {declared_hash!r} != "
            f"bound {stage.manifest_hash!r}"
        )
    files = manifest.get("files_sha256")
    if not isinstance(files, dict):
        raise UniverseInputError(f"{stage.stage} manifest lacks files_sha256")
    for binding in stage.files:
        declared = files.get(binding.relative_path)
        if declared != binding.sha256:
            raise UniverseBindingError(
                f"{stage.stage} manifest binds {binding.relative_path} to "
                f"{declared!r}, expected {binding.sha256!r}"
            )
        blob = resolver.read_bytes(binding.relative_path)
        actual = _sha256_hex(blob)
        if actual != binding.sha256:
            raise UniverseBindingError(
                f"{stage.stage} file {binding.relative_path} hash {actual} != "
                f"bound {binding.sha256}"
            )
    return manifest


# ---------------------------------------------------------------------------
# Input preparation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Key:
    security_key: str
    ticker: str
    exchange: str | None
    listed_start: date | None
    listed_end: date | None
    state: str


@dataclass(frozen=True)
class _TickerData:
    usable_idx: np.ndarray
    liq_idx: np.ndarray
    liq_dv: np.ndarray


@dataclass(frozen=True)
class Schedule:
    calendar: Any
    full: tuple[date, ...]
    development: tuple[date, ...]
    reserve: tuple[date, ...]

    def __post_init__(self) -> None:
        if len(self.full) != N_FULL:
            raise D6ConsistencyError(
                f"full schedule has {len(self.full)} dates, expected {N_FULL}"
            )
        if self.full[0] != date(2016, 1, 29) or self.full[-1] != date(2026, 8, 31):
            raise D6ConsistencyError(
                f"full schedule boundaries {self.full[0]}..{self.full[-1]} "
                "are not 2016-01-29..2026-08-31"
            )
        if len(self.development) != N_DEVELOPMENT:
            raise D6ConsistencyError(
                f"development schedule has {len(self.development)} dates, "
                f"expected {N_DEVELOPMENT}"
            )
        if self.development[-1] != DEVELOPMENT_END:
            raise D6ConsistencyError(
                f"last development formation {self.development[-1]} is not "
                f"{DEVELOPMENT_END}"
            )
        if len(self.reserve) != N_RESERVE:
            raise D6ConsistencyError(
                f"reserve schedule has {len(self.reserve)} dates, "
                f"expected {N_RESERVE}"
            )
        if self.reserve[0] != date(2023, 7, 31):
            raise D6ConsistencyError(
                f"first reserve formation {self.reserve[0]} is not 2023-07-31"
            )
        if self.reserve[-1] != date(2026, 8, 31):
            raise D6ConsistencyError(
                f"last reserve formation {self.reserve[-1]} is not 2026-08-31"
            )


def build_schedule(calendar: Any) -> Schedule:
    """Generate the full / development / reserve formation schedule."""
    full_ts = calendar.month_end_trading_dates(SCHEDULE_START, SCHEDULE_END)
    full = tuple(_as_date(ts) for ts in full_ts)
    development = tuple(d for d in full if d <= DEVELOPMENT_END)
    reserve = tuple(d for d in full if d >= RESERVE_START)
    return Schedule(
        calendar=calendar,
        full=full,
        development=development,
        reserve=reserve,
    )


def _prepare(
    resolver: Resolver, bindings: Bindings, calendar: Any
) -> tuple[tuple[_Key, ...], Mapping[str, _TickerData], dict, dict, np.ndarray, dict]:
    _verify_stage(resolver, bindings.d3)
    _verify_stage(resolver, bindings.d4)
    _verify_stage(resolver, bindings.d5)
    _verify_stage(resolver, bindings.dsc)

    observations = pd.read_parquet(
        resolver.resolve(bindings.d3.files[0].relative_path),
        columns=["ticker", "date", "close", "volume", "raw_row_index"],
    )
    identity = pd.read_parquet(
        resolver.resolve(bindings.d4.files[0].relative_path),
        columns=[
            "security_key",
            "ticker",
            "exchange",
            "listed_start",
            "listed_end",
            "state",
        ],
    )
    day_flags = pd.read_parquet(
        resolver.resolve(bindings.d5.files[0].relative_path),
        columns=[
            "ticker",
            "date",
            "raw_row_index",
            "class_or_flag",
            "disposition",
        ],
    )
    class_timeline = _load_timeline(
        resolver.resolve(bindings.dsc.files[0].relative_path)
    )
    policy_timeline = _load_timeline(
        resolver.resolve(bindings.dsc.files[1].relative_path)
    )

    # --- post-load reserve assertions (task spec §3) --------------------
    _assert_development_dates(observations["date"], "D3 observations")
    _assert_development_dates(day_flags["date"], "D5 day flags")

    cal_dates = tuple(_as_date(ts) for ts in calendar.dates)
    pos = {d: i for i, d in enumerate(cal_dates)}
    d64 = np.array([np.datetime64(d) for d in cal_dates], dtype="datetime64[D]")

    keys = tuple(_key_from_row(row) for row in identity.to_dict("records"))
    seen: set[str] = set()
    for key in keys:
        if key.security_key in seen:
            raise D6ConsistencyError(f"duplicate security_key {key.security_key!r}")
        seen.add(key.security_key)

    unusable = _unusable_keys(day_flags)
    tickers = _prepare_tickers(observations, unusable, pos)
    return keys, tickers, class_timeline, policy_timeline, d64, pos


def _key_from_row(row: Mapping[str, Any]) -> _Key:
    return _Key(
        security_key=str(row["security_key"]),
        ticker=str(row["ticker"]),
        exchange=None if row["exchange"] is None else str(row["exchange"]),
        listed_start=_as_date(row["listed_start"]),
        listed_end=_as_date(row["listed_end"]),
        state=str(row["state"]),
    )


def _assert_development_dates(series: pd.Series, what: str) -> None:
    if len(series) == 0:
        return
    parsed = pd.to_datetime(series, errors="coerce")
    if bool((parsed >= pd.Timestamp(RESERVE_START)).any()):
        raise ReserveFirewallError(
            f"{what} contains a date on or after {RESERVE_START}"
        )


def _unusable_keys(day_flags: pd.DataFrame) -> set[tuple[str, date, int]]:
    unusable: set[tuple[str, date, int]] = set()
    for row in day_flags.to_dict("records"):
        disposition = row["disposition"]
        klass = row["class_or_flag"]
        if (
            disposition == DISPOSITION_CORRUPT
            or klass == CLASS_INTERVAL_MISMATCH
            or klass == CLASS_VENDOR_NULL
        ):
            d = _as_date(row["date"])
            idx = _as_int(row["raw_row_index"])
            if d is None or idx is None:
                continue
            unusable.add((str(row["ticker"]), d, idx))
    return unusable


def _prepare_tickers(
    observations: pd.DataFrame,
    unusable: set[tuple[str, date, int]],
    pos: Mapping[date, int],
) -> dict[str, _TickerData]:
    usable: dict[str, set[int]] = {}
    liquidity: dict[str, dict[int, float]] = {}
    for row in observations.to_dict("records"):
        d = _as_date(row["date"])
        if d is None or d not in pos:
            continue
        idx = _as_int(row["raw_row_index"])
        if idx is None:
            continue
        ticker = str(row["ticker"])
        session = pos[d]
        if (ticker, d, idx) in unusable:
            continue
        bucket = usable.setdefault(ticker, set())
        if session in bucket:
            raise D6ConsistencyError(
                f"ticker {ticker!r} has two usable rows on session {d}"
            )
        bucket.add(session)
        close = _float_or_none(row["close"])
        volume = _as_int(row["volume"])
        if close is not None and close > 0 and volume is not None:
            liquidity.setdefault(ticker, {})[session] = close * float(volume)

    result: dict[str, _TickerData] = {}
    for ticker in usable:
        u = np.array(sorted(usable[ticker]), dtype=np.int64)
        liq_map = liquidity.get(ticker, {})
        liq_idx = np.array(sorted(liq_map), dtype=np.int64)
        liq_dv = np.array([liq_map[i] for i in sorted(liq_map)], dtype=np.float64)
        result[ticker] = _TickerData(usable_idx=u, liq_idx=liq_idx, liq_dv=liq_dv)
    return result


def _load_timeline(
    path: Path,
) -> dict[str, list[dict[str, Any]]]:
    """Load one D-SC timeline, mirroring the classification public loader.

    Segments are order-independent: each key's list is stably sorted by
    ``knowledge_date`` so the query API never depends on file row order.
    """
    frame = pd.read_parquet(path)
    mapping: dict[str, list[dict[str, Any]]] = {}
    for record in frame.to_dict("records"):
        record["knowledge_date"] = _as_date(record["knowledge_date"])
        mapping.setdefault(str(record["security_key"]), []).append(record)
    for segments in mapping.values():
        segments.sort(key=lambda segment: segment["knowledge_date"])
    return mapping


# ---------------------------------------------------------------------------
# Per-formation evaluation
# ---------------------------------------------------------------------------


def _in_population(key: _Key, f: date, cutoff: date) -> bool:
    return (
        key.listed_start is not None
        and key.listed_end is not None
        and key.listed_start <= cutoff
        and key.listed_end >= f
    )


def _evaluate_key(
    key: _Key,
    f: date,
    cutoff: date,
    p: int,
    tickers: Mapping[str, _TickerData],
    class_timeline: Mapping[str, list[dict[str, Any]]],
    policy_timeline: Mapping[str, list[dict[str, Any]]],
    d64: np.ndarray,
) -> dict[str, Any]:
    assert key.listed_start is not None
    lo_date = key.listed_start
    if lo_date < ACQUISITION_START:
        lo_date = ACQUISITION_START
    lo_idx = int(np.searchsorted(d64, np.datetime64(lo_date), side="left"))
    hi_idx = p - 1
    listing_age = hi_idx - lo_idx + 1 if lo_idx <= hi_idx else 0

    ticker = tickers.get(key.ticker)
    first: int | None = None
    usable_pos = 0
    if ticker is not None and len(ticker.usable_idx):
        usable_pos = int(np.searchsorted(ticker.usable_idx, lo_idx, side="left"))
        if (
            usable_pos < len(ticker.usable_idx)
            and int(ticker.usable_idx[usable_pos]) <= hi_idx
        ):
            first = int(ticker.usable_idx[usable_pos])
    if first is None:
        denominator = 0
        numerator = 0
    else:
        denominator = hi_idx - first + 1
        usable_in = (
            int(np.searchsorted(ticker.usable_idx, hi_idx, side="right"))
            - usable_pos
        )
        numerator = denominator - usable_in

    trailing = 0
    median_dv: float | None = None
    if ticker is not None and len(ticker.liq_idx):
        wlo = p - LIQUIDITY_WINDOW
        whi = p - 1
        a = int(np.searchsorted(ticker.liq_idx, wlo, side="left"))
        b = int(np.searchsorted(ticker.liq_idx, whi, side="right"))
        trailing = b - a
        if trailing > 0:
            median_dv = float(np.median(ticker.liq_dv[a:b]))

    if cutoff > DSC_MAX_CUTOFF:
        raise ReserveFirewallError(
            f"D-SC query cutoff {cutoff} exceeds {DSC_MAX_CUTOFF}"
        )
    class_state, class_reason = class_state_at(
        class_timeline, key.security_key, cutoff
    )
    policy_state, _ = policy_state_at(policy_timeline, key.security_key, cutoff)

    if key.state != STATE_INTERVAL_ASSIGNED:
        reason = REASON_IDENTITY_NOT_ADMISSIBLE
    elif key.exchange not in ELIGIBLE_EXCHANGES:
        reason = REASON_EXCHANGE_NOT_ELIGIBLE
    elif class_state != CLASS_COMMON_STOCK:
        reason = REASON_NOT_COMMON_STOCK
    elif policy_state == POLICY_EXCLUDED:
        reason = REASON_ISSUER_POLICY_EXCLUDED
    elif policy_state == POLICY_UNRESOLVED:
        reason = REASON_ISSUER_POLICY_UNRESOLVED
    elif listing_age < MIN_LISTING_AGE_SESSIONS:
        reason = REASON_LISTING_AGE_INSUFFICIENT
    elif denominator <= 0:
        reason = REASON_PIT_MISSINGNESS_EMPTY
    elif numerator * 100 > 5 * denominator:
        reason = REASON_PIT_MISSINGNESS_EXCEEDED
    elif trailing < MIN_LIQUIDITY_SESSIONS:
        reason = REASON_TRAILING_LIQUIDITY_INSUFFICIENT
    else:
        reason = REASON_ELIGIBLE

    return {
        "security_key": key.security_key,
        "ticker": key.ticker,
        "identity_state": key.state,
        "class_state": class_state,
        "class_reason": class_reason,
        "policy_state": policy_state,
        "listing_age_sessions": listing_age,
        "missing_numerator": numerator,
        "missing_denominator": denominator,
        "trailing_valid_sessions": trailing,
        "median_dv": median_dv,
        "reason": reason,
        "rank": None,
    }


def _evaluate_formation(
    f: date,
    cutoff: date,
    keys: Sequence[_Key],
    tickers: Mapping[str, _TickerData],
    class_timeline: Mapping[str, list[dict[str, Any]]],
    policy_timeline: Mapping[str, list[dict[str, Any]]],
    d64: np.ndarray,
    pos: Mapping[date, int],
    n_keys: int,
) -> dict[str, Any]:
    p = pos[f]
    population = [key for key in keys if _in_population(key, f, cutoff)]
    not_listed = n_keys - len(population)
    evaluations = [
        _evaluate_key(
            key, f, cutoff, p, tickers, class_timeline, policy_timeline, d64
        )
        for key in population
    ]
    eligible = [e for e in evaluations if e["reason"] == REASON_ELIGIBLE]
    eligible.sort(key=lambda e: (-e["median_dv"], e["security_key"]))
    for rank, evaluation in enumerate(eligible, start=1):
        evaluation["rank"] = rank
    n_eligible = len(eligible)
    status = STATUS_OK if n_eligible >= MIN_ELIGIBLE else STATUS_UNIVERSE_SHORTFALL
    return {
        "formation_date": f,
        "cutoff_date": cutoff,
        "n_population": len(population),
        "n_not_listed_at_formation": not_listed,
        "n_eligible": n_eligible,
        "status": status,
        "top_1000_shortfall": n_eligible < 1000,
        "gate11": "PASS" if status == STATUS_OK else "FAIL",
        "evaluations": evaluations,
        "universe_rows": [
            evaluation for evaluation in eligible if evaluation["rank"] <= 1000
        ],
    }


# ---------------------------------------------------------------------------
# Output construction
# ---------------------------------------------------------------------------


def _compute_universe_id(
    bindings: Bindings, documents: Mapping[str, str], code_sha: str
) -> str:
    payload = {
        "bindings": {
            stage.stage: {
                "artifact_id": stage.artifact_id,
                "hash": stage.manifest_hash,
                "files_sha256": {
                    binding.relative_path: binding.sha256
                    for binding in stage.files
                },
            }
            for stage in bindings.stages()
        },
        "documents": dict(documents),
        "universe_code_sha256": code_sha,
    }
    digest = _sha256_hex(canonical_json(payload).encode("utf-8"))
    return "u-" + digest[:16]


def _build_universe_development(results: Sequence[dict[str, Any]]) -> pd.DataFrame:
    records: list[list[Any]] = []
    for result in results:
        f = result["formation_date"]
        for evaluation in result["universe_rows"]:
            rank = evaluation["rank"]
            records.append(
                [
                    f,
                    evaluation["security_key"],
                    rank,
                    rank <= 200,
                    rank <= 500,
                    rank <= 1000,
                ]
            )
    frame = _build_frame(records, _UNIVERSE_DEV_COLUMNS, _UNIVERSE_DEV_DTYPES)
    if not frame.empty:
        frame = frame.sort_values(
            ["formation_date", "rank"], kind="mergesort", ignore_index=True
        )
    return frame


def _build_audit(results: Sequence[dict[str, Any]]) -> pd.DataFrame:
    records: list[list[Any]] = []
    for result in results:
        f = result["formation_date"]
        for evaluation in result["evaluations"]:
            records.append(
                [
                    f,
                    evaluation["security_key"],
                    evaluation["ticker"],
                    evaluation["identity_state"],
                    evaluation["class_state"],
                    evaluation["class_reason"],
                    evaluation["policy_state"],
                    evaluation["listing_age_sessions"],
                    evaluation["missing_numerator"],
                    evaluation["missing_denominator"],
                    evaluation["trailing_valid_sessions"],
                    evaluation["median_dv"],
                    evaluation["reason"],
                    evaluation["rank"],
                ]
            )
    frame = _build_frame(records, _AUDIT_COLUMNS, _AUDIT_DTYPES)
    if not frame.empty:
        frame = frame.sort_values(
            ["formation_date", "security_key"], kind="mergesort", ignore_index=True
        )
    return frame


def _build_formations(
    schedule: Schedule, results: Sequence[dict[str, Any]]
) -> pd.DataFrame:
    by_date = {result["formation_date"]: result for result in results}
    records: list[list[Any]] = []
    for f in schedule.full:
        cutoff = _as_date(schedule.calendar.previous_trading_day(f))
        if f in by_date:
            result = by_date[f]
            records.append(
                [
                    f,
                    cutoff,
                    PARTITION_DEVELOPMENT,
                    result["n_population"],
                    result["n_eligible"],
                    result["status"],
                    result["top_1000_shortfall"],
                ]
            )
        else:
            records.append(
                [f, cutoff, PARTITION_RESERVE, None, None, STATUS_DEFERRED, None]
            )
    return _build_frame(records, _FORMATIONS_COLUMNS, _FORMATIONS_DTYPES)


def _formation_summary(
    schedule: Schedule, results: Sequence[dict[str, Any]]
) -> list[dict[str, Any]]:
    by_date = {result["formation_date"]: result for result in results}
    summary: list[dict[str, Any]] = []
    for f in schedule.full:
        cutoff = _as_date(schedule.calendar.previous_trading_day(f))
        if f in by_date:
            result = by_date[f]
            summary.append(
                {
                    "formation_date": f.isoformat(),
                    "cutoff_date": cutoff.isoformat(),
                    "partition": PARTITION_DEVELOPMENT,
                    "n_population": result["n_population"],
                    "n_not_listed_at_formation": result[
                        "n_not_listed_at_formation"
                    ],
                    "n_eligible": result["n_eligible"],
                    "status": result["status"],
                    "top_1000_shortfall": result["top_1000_shortfall"],
                    "gate11": result["gate11"],
                }
            )
        else:
            summary.append(
                {
                    "formation_date": f.isoformat(),
                    "cutoff_date": cutoff.isoformat(),
                    "partition": PARTITION_RESERVE,
                    "n_population": None,
                    "n_not_listed_at_formation": None,
                    "n_eligible": None,
                    "status": STATUS_DEFERRED,
                    "top_1000_shortfall": None,
                    "gate11": "DEFERRED / NOT ASSESSED",
                }
            )
    return summary


def _schedule_summary(schedule: Schedule) -> dict[str, Any]:
    return {
        "calendar_start": CALENDAR_START.isoformat(),
        "calendar_end": CALENDAR_END.isoformat(),
        "schedule_start": SCHEDULE_START.isoformat(),
        "schedule_end": SCHEDULE_END.isoformat(),
        "full": {
            "count": len(schedule.full),
            "first": schedule.full[0].isoformat(),
            "last": schedule.full[-1].isoformat(),
        },
        "development": {
            "count": len(schedule.development),
            "first": schedule.development[0].isoformat(),
            "last": schedule.development[-1].isoformat(),
        },
        "reserve": {
            "count": len(schedule.reserve),
            "first": schedule.reserve[0].isoformat(),
            "last": schedule.reserve[-1].isoformat(),
        },
    }


def _bindings_summary(bindings: Bindings) -> dict[str, Any]:
    return {
        stage.stage: {
            "artifact_id": stage.artifact_id,
            "manifest_path": stage.manifest_relative_path,
            "manifest_hash_field": stage.manifest_hash_field,
            "hash": stage.manifest_hash,
            "files_sha256": {
                binding.relative_path: binding.sha256 for binding in stage.files
            },
        }
        for stage in bindings.stages()
    }


def _write_outputs(
    root: Path,
    universe_id: str,
    development: pd.DataFrame,
    formations: pd.DataFrame,
    audit: pd.DataFrame,
    content: dict[str, Any],
) -> dict[str, Any]:
    derived = root / "derived" / "universe"
    derived.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=universe_id + ".", dir=derived))
    try:
        development_path = stage / DEVELOPMENT_FILE
        formations_path = stage / FORMATIONS_FILE
        audit_path = stage / AUDIT_FILE
        development.to_parquet(
            development_path, engine="pyarrow", index=False, compression="zstd"
        )
        formations.to_parquet(
            formations_path, engine="pyarrow", index=False, compression="zstd"
        )
        audit.to_parquet(
            audit_path, engine="pyarrow", index=False, compression="zstd"
        )
        files_sha256 = {
            f"derived/universe/{universe_id}/{DEVELOPMENT_FILE}": _sha256_file(
                development_path
            ),
            f"derived/universe/{universe_id}/{FORMATIONS_FILE}": _sha256_file(
                formations_path
            ),
            f"derived/universe/{universe_id}/{AUDIT_FILE}": _sha256_file(audit_path),
        }
        content["files_sha256"] = files_sha256
        content["universe_hash"] = _sha256_hex(
            canonical_json(files_sha256).encode("utf-8")
        )
        (stage / REPORT_FILE).write_bytes(_canonical_bytes(content))

        target = derived / universe_id
        if target.exists():
            existing = sorted(p.name for p in target.iterdir())
            produced = sorted(p.name for p in stage.iterdir())
            if existing != produced or any(
                (target / name).read_bytes() != (stage / name).read_bytes()
                for name in produced
            ):
                raise NondeterministicUniverseError(
                    f"existing {target} holds different bytes for {universe_id}"
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


def run_universe(
    root: Path | str,
    *,
    bindings: Bindings | None = None,
    calendar: Any | None = None,
) -> dict[str, Any]:
    """Build the development D6 universe and return its ``universe.json``."""
    bindings = bindings if bindings is not None else DEFAULT_BINDINGS
    if not isinstance(bindings, Bindings):
        raise UniverseInputError("bindings must be a Bindings value")
    calendar = (
        calendar
        if calendar is not None
        else build_nyse_calendar(CALENDAR_START, CALENDAR_END)
    )
    root = Path(root)
    resolver = Resolver(root, bindings)
    documents = _verify_documents(bindings)
    code_sha = _sha256_hex(Path(__file__).read_bytes())
    universe_id = _compute_universe_id(bindings, documents, code_sha)

    (
        keys,
        tickers,
        class_timeline,
        policy_timeline,
        d64,
        pos,
    ) = _prepare(resolver, bindings, calendar)
    schedule = build_schedule(calendar)

    results = [
        _evaluate_formation(
            f,
            _as_date(calendar.previous_trading_day(f)),
            keys,
            tickers,
            class_timeline,
            policy_timeline,
            d64,
            pos,
            len(keys),
        )
        for f in schedule.development
    ]

    development = _build_universe_development(results)
    formations = _build_formations(schedule, results)
    audit = _build_audit(results)

    all_ok = all(result["status"] == STATUS_OK for result in results)
    content: dict[str, Any] = {
        "schema": SCHEMA,
        "universe_id": universe_id,
        "universe_code_sha256": code_sha,
        "bindings": _bindings_summary(bindings),
        "documents": dict(documents),
        "schedule": _schedule_summary(schedule),
        "n_keys": len(keys),
        "formations": _formation_summary(schedule, results),
        "gate_11": {
            "development": "PASS" if all_ok else "FAIL",
            "reserve_gate11": "DEFERRED / NOT ASSESSED",
        },
        "certification_boundary": CERTIFICATION_BOUNDARY,
    }
    return _write_outputs(root, universe_id, development, formations, audit, content)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the D6 development PIT universe.")
    parser.add_argument("--root", required=True, help="data_store root")
    args = parser.parse_args(argv)
    content = run_universe(args.root)
    print(content["universe_id"])
    print(content["universe_hash"])
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
