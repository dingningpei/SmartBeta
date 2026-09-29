"""D8 (ii): the reserve package's footprint, payload and ARTIFACT registration.

This module owns **only** the reserve-package footprint and the
Knowledge-PIT ``ARTIFACT`` registration frozen by
``worker_tasks/dataset_v1/task-d8-reserve-firewall.md`` sections 3-6 (authority
order: contract sections 12/14/16-gate-12/20; the D8 clarification; the frozen
Phase-10 plan sections 5.1/5.2/5.4/6.2/6.5/13.1).

It computes the Phase-10 footprint of the already-possessed D3 reserve package
``reserve_sealed/n-f874d3ecb61374d4/`` from its **key columns only** (plus
parquet schema metadata), through the unmodified
:func:`smart_beta.science.footprint.footprint_from_panel`, and registers one
``ARTIFACT`` record in the research-environment Knowledge-PIT log.

Fail-closed rules (task spec section 6):

* every binding, the four pinned Phase-10 code hashes and every reserve file's
  SHA-256 are verified by streaming bytes before any key is read;
* the reserve ``observations`` schema must equal the frozen 16 names;
* the ``obs_flags`` and ``ca_events`` key domains must be subsets of the
  ``observations`` keys;
* only three test log modes exist -- ``TEST`` (never the production path),
  ``DRY_RUN`` (a tmp copy) and ``PRODUCTION`` (exact path plus token);
* the log is append-only, never rewritten, never repaired and never gains an
  ``ACCESS`` or ``EXPOSURE_DECLARATION`` record.

No provider, network or credential access is made.  Phase 10 is used
read-only.
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Mapping, Sequence

import pandas as pd
import pyarrow.parquet as pq

from smart_beta.dataset_v1.normalize import OBSERVATION_COLUMNS
from smart_beta.dataset_v1.reserve_firewall import D8FirewallError
from smart_beta.science.contracts import (
    DERIVATION_RULES_VERSION,
    Channel,
    ObservationKind,
    RecordKind,
    content_hash,
)
from smart_beta.science.footprint import (
    Footprint,
    FootprintOverlap,
    footprint_from_panel,
    overlap,
)
from smart_beta.science.knowledge import (
    GENESIS_PREV_HASH,
    KnowledgeLog,
    KnowledgeLockError,
    KnowledgeRecord,
    TruncatedTail,
    read_records,
)
from smart_beta.vendors.tiingo.calendar_source import build_nyse_calendar

__all__ = [
    # constants
    "D3_BUILD_ID",
    "D3_NORMALIZED_HASH",
    "D3_BUILD_JSON_SHA256",
    "IDENTITY_ID",
    "IDENTITY_HASH",
    "IDENTITY_JSON_SHA256",
    "IDENTITY_ASSIGNMENT_SHA256",
    "SMOKE_BATCH",
    "PRODUCTION_BATCH",
    "SOURCE_LABEL",
    "VARIABLE_MAP",
    "PRODUCTION_AUTHORIZATION",
    "RegistrationMode",
    # errors
    "D8Error",
    "D8HoldError",
    "D8ConflictError",
    "D8RegistrationModeError",
    # bindings
    "ReserveBindings",
    "default_bindings",
    # API
    "RegistrationResult",
    "register_reserve_artifact",
    "main",
]

_REPO_ROOT = Path(__file__).resolve().parents[2]

D3_BUILD_ID = "n-f874d3ecb61374d4"
D3_NORMALIZED_HASH = (
    "f93093a8b8fa07521191d687560c2c26ab16630ebeed35d68aa12867def47fb3"
)
D3_BUILD_JSON_SHA256 = (
    "f11d9b05d5cbe2a736d991ea90d4b56f469f855f33b4168a4a4af14b87666450"
)
IDENTITY_ID = "i-a21f1e46621dee94"
IDENTITY_HASH = (
    "1023dbca39fd061d52fd26eeb6302f4a8dec467a8ea996b1903da801306ae730"
)
IDENTITY_JSON_SHA256 = (
    "b8ed3d23979f8cbb9f320fae96a8cea968b16332fd1000fa1cdedca15df43aa4"
)
IDENTITY_ASSIGNMENT_SHA256 = (
    "fee4614009095773f75fb832343a5fa9b87b9de1cf363125567222b08fad3f8b"
)
SMOKE_BATCH = "b-10cd8b2e2ef11a98"
PRODUCTION_BATCH = "b-c21b48190918f21f"
SOURCE_LABEL = f"discovery_dataset_v1:reserve:{D3_BUILD_ID}"
PRODUCTION_AUTHORIZATION = "D8-PRODUCTION-REGISTRATION-AUTHORIZED"

#: The reserve package file hashes, bound in the clarification (section 3).
_RESERVE_FILE_SHA256: dict[str, str] = {
    "ca_events": (
        "43c3bd8537c2aa59205b4d2b603b98a61eaec29cb71f44daeb4ab389c4fde2d7"
    ),
    "obs_flags": (
        "3e955395f8c1eb21ae9d192debbe2bfa2c8e6cdf3a6fe9a404759dc8686a9225"
    ),
    "observations": (
        "f7621c9985eab6eeaed18d40f82d33ad37b0115f7155fcb41e436446f1687586"
    ),
}

#: The pinned Phase-10 code SHA-256 values (task spec section 1).
_PINNED_CODE_SHA256: dict[str, str] = {
    "smart_beta/science/knowledge.py": (
        "7a4ab759ab20d8acf945a1de3246653a572069696a2f2bf791d155c6b6932fec"
    ),
    "smart_beta/science/footprint.py": (
        "779dfd1e691279d081f5bc935d61957734a901ac9783fc695fa168858fe26a88"
    ),
    "smart_beta/science/contracts.py": (
        "8e8b2f9c4f133e34f09794fedc094c80198ecf64440c727e13209b43488dbd2d"
    ),
    "smart_beta/vendors/tiingo/calendar_source.py": (
        "79bca35ddd46f87102e90f6ce0c2d51a8b0d16f1fe9caf56b7674b43fce8e23f"
    ),
}

#: The exact manifest batch directory set (task spec section 1).
_MANIFEST_FILES = ("batch.json", "status.json", "attempts.jsonl")
_DEFAULT_MANIFEST_HASHES: dict[str, dict[str, str]] = {
    SMOKE_BATCH: {
        "attempts.jsonl": (
            "1b54b93df22c7dd459d19c8455251a28a86898adc81045e1f161c8e9e4a04549"
        ),
        "batch.json": (
            "42c3d7c3e132a9116d50b083f493b22e8dc9b6ecd142f4a5b9b356b8117fab18"
        ),
        "status.json": (
            "4f7b59cf0d91b08c46432cf7c984c525dfeec92712872c7cc6fad361f8b87ca3"
        ),
    },
    PRODUCTION_BATCH: {
        "attempts.jsonl": (
            "197896599e9127eab75792bf2f2a547b72dcf53f109c27565519d9a91608d6a6"
        ),
        "batch.json": (
            "0cc240a4ee813493e85424f75e3829c9df437938fbeac9b297fbff434a028214"
        ),
        "status.json": (
            "595a612cc6daecd3c438696876952fcffbf917abb596d874b383c50f56b1fe3d"
        ),
    },
}

#: The frozen VariableMap (clarification section 4): exact D3 column names,
#: each entry ``{"derived_variable": <name>}``.  The Phase-10 derivation rules
#: turn ``PRICE_FIELD`` into ``PRICE_LEVEL``, ``ACTIVITY_FIELD`` into
#: ``TRADING_ACTIVITY`` and ``RETURN_1D`` into ``PRICE_CHANGE``.
VARIABLE_MAP: dict[str, dict[str, str]] = {
    "open": {"derived_variable": "PRICE_FIELD"},
    "high": {"derived_variable": "PRICE_FIELD"},
    "low": {"derived_variable": "PRICE_FIELD"},
    "close": {"derived_variable": "PRICE_FIELD"},
    "adjOpen": {"derived_variable": "PRICE_FIELD"},
    "adjHigh": {"derived_variable": "PRICE_FIELD"},
    "adjLow": {"derived_variable": "PRICE_FIELD"},
    "adjClose": {"derived_variable": "PRICE_FIELD"},
    "volume": {"derived_variable": "ACTIVITY_FIELD"},
    "adjVolume": {"derived_variable": "ACTIVITY_FIELD"},
    "divCash": {"derived_variable": "RETURN_1D"},
    "splitFactor": {"derived_variable": "RETURN_1D"},
}

#: The provenance columns that identify a vendor record and its bytes; they are
#: not observations of any subject and are excluded from the frame.
_PROVENANCE_COLUMNS = frozenset({"raw_row_index", "raw_sha256"})

_RESERVE_START_ISO = "2023-07-03"

_SEALED_SEMANTICS = (
    "from the D8 registration boundary onward, the artifact is designated "
    "sealed reserve evidence and is not authorized for development or "
    "evaluation access, except through a separately authorized confirmation "
    "procedure. sealed=true does not assert historical non-exposure"
)

_HISTORICAL_DISCLOSURE: tuple[tuple[str, str], ...] = (
    (
        "D2",
        "acquired raw source bodies spanning development and reserve (both "
        "batches)",
    ),
    (
        "D3",
        "parsed every raw row, including reserve values; created the reserve "
        "partition",
    ),
    ("D5 (gate 10)", "rebuild re-read raw data, including reserve values"),
    (
        "D5 (L1)",
        "reserve-period OHLC and value checks and CORP_ACTION_SUSPECT "
        "calculations",
    ),
    (
        "D5 (outputs)",
        "persisted reserve-derived `day_flags.parquet` and reserve counts in "
        "`qc_report.json`",
    ),
    (
        "D-SC",
        "read reserve-period structural fields, flags, dates and names; "
        "produced the structural eligibility measurements disclosed in D6 "
        "clarification H1",
    ),
    ("D6, D7a, D7b", "no production reserve read (file-open audits)"),
    ("Contract \u00a712", "LLM pre-training exposure disclosed"),
    (
        "D8",
        "packaging hash verification (bytes) and the one metadata-only "
        "key-column read (\u00a75)",
    ),
)

_EVIDENCE_ROLE = (
    "UNKNOWN_EXPOSURE (not upgraded by registration; absence of ACCESS is "
    "not proof of no access)"
)

#: The projected key columns per component (the one authorized reserve read).
_PROJECTED_COLUMNS: dict[str, tuple[str, ...]] = {
    "observations": ("ticker", "date"),
    "obs_flags": ("ticker", "date"),
    "ca_events": ("stock_id", "effective_date"),
}


# ---------------------------------------------------------------------------
# Typed errors (fail closed)
# ---------------------------------------------------------------------------


class D8Error(Exception):
    """Base class for every D8 reserve-artifact error."""


class D8HoldError(D8Error):
    """A D8 HOLD: the frozen preconditions are not met; no append."""


class D8ConflictError(D8Error):
    """A same-``source_label`` ARTIFACT differs; no append."""


class D8RegistrationModeError(D8HoldError):
    """A registration mode refused the path or token; no append."""


# ---------------------------------------------------------------------------
# Bindings
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReserveBindings:
    """The frozen reserve-package bindings; production defaults, tests inject."""

    build_id: str = D3_BUILD_ID
    normalized_hash: str = D3_NORMALIZED_HASH
    build_json_sha256: str = D3_BUILD_JSON_SHA256
    reserve_files: Mapping[str, str] = field(
        default_factory=lambda: dict(_RESERVE_FILE_SHA256)
    )
    identity_id: str = IDENTITY_ID
    identity_hash: str = IDENTITY_HASH
    identity_json_sha256: str = IDENTITY_JSON_SHA256
    identity_assignment_sha256: str = IDENTITY_ASSIGNMENT_SHA256
    manifests: Mapping[str, Mapping[str, str]] = field(
        default_factory=lambda: {
            batch: dict(files) for batch, files in _DEFAULT_MANIFEST_HASHES.items()
        }
    )
    code_hashes: Mapping[str, str] = field(
        default_factory=lambda: dict(_PINNED_CODE_SHA256)
    )
    spec_sha256: str = ""
    clarification_sha256: str = ""


def _sha256_file(path: Path, *, chunk_size: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def default_bindings() -> ReserveBindings:
    """The frozen production bindings (tests inject synthetic store values)."""
    spec_path = (
        _REPO_ROOT / "worker_tasks" / "dataset_v1" / "task-d8-reserve-firewall.md"
    )
    clarification_path = (
        _REPO_ROOT / "docs" / "discovery_dataset_v1_1_d8_clarification.md"
    )
    spec_sha256 = (
        _sha256_file(spec_path) if spec_path.is_file() else ""
    )
    clarification_sha256 = (
        _sha256_file(clarification_path)
        if clarification_path.is_file()
        else ""
    )
    return ReserveBindings(
        spec_sha256=spec_sha256,
        clarification_sha256=clarification_sha256,
    )


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def _reserve_path(root: Path, bindings: ReserveBindings, component: str) -> Path:
    return root / "reserve_sealed" / bindings.build_id / f"{component}.parquet"


def _production_log_path(root: Path) -> Path:
    return (root.resolve().parent / "knowledge_pit" / "knowledge.jsonl")


def _as_date_cell(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, pd.Timestamp):
        # pandas Timestamp subclasses datetime/date; check Timestamp first.
        return value
    if isinstance(value, pd.Timestamp):
        if pd.isna(value):
            return None
        return value.date()
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    try:
        return pd.Timestamp(value).date()
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# The schema-backed key frame
# ---------------------------------------------------------------------------


class _KeyProjection:
    """The minimal ``[[subject, date]]`` projection handed to Phase 10."""

    def __init__(self, rows: Sequence[tuple[str, date]]) -> None:
        self._rows = tuple(rows)

    def to_dict(self, orient: str = "records") -> list[dict[str, Any]]:
        if orient != "records":
            raise D8FirewallError(
                f"_KeyProjection only supports orient='records', got {orient!r}"
            )
        return [
            {"subject": subject, "date": row_date}
            for subject, row_date in self._rows
        ]


class _SchemaBackedKeyFrame:
    """A key-only frame that can never materialize an economic value.

    ``.columns`` are the frozen schema names minus the provenance columns,
    with ``ticker`` renamed to ``subject`` (``subject``, ``date`` and the
    economic columns).  Only ``frame[["subject", "date"]]`` is served; any
    other request raises :class:`D8FirewallError` so a value column is never
    materializable.
    """

    def __init__(
        self,
        schema_names: Sequence[str],
        key_rows: Sequence[tuple[str, date]],
    ) -> None:
        self._schema_names = tuple(schema_names)
        self._key_rows = tuple(key_rows)

    @property
    def columns(self) -> tuple[str, ...]:
        names: list[str] = []
        for name in self._schema_names:
            if name in _PROVENANCE_COLUMNS:
                continue
            names.append("subject" if name == "ticker" else name)
        return tuple(names)

    def to_dict(self, orient: str = "records") -> list[dict[str, Any]]:
        """The key projection as records (the frame is key-only)."""
        return _KeyProjection(self._key_rows).to_dict(orient)

    def __len__(self) -> int:
        return len(self._key_rows)

    def __getitem__(self, key: Any) -> _KeyProjection:
        if isinstance(key, (str, bytes)) or not isinstance(key, Sequence):
            raise D8FirewallError(
                "only the ['subject', 'date'] key projection may be read"
            )
        if tuple(key) != ("subject", "date"):
            raise D8FirewallError(
                f"refusing to materialize non-key columns: {list(key)!r}"
            )
        return _KeyProjection(self._key_rows)


# ---------------------------------------------------------------------------
# Verification helpers
# ---------------------------------------------------------------------------


def _verify_code_hashes(bindings: ReserveBindings) -> None:
    for relative, expected in bindings.code_hashes.items():
        path = _REPO_ROOT / relative
        if not path.is_file():
            raise D8HoldError(f"pinned Phase-10 file is missing: {relative}")
        actual = _sha256_file(path)
        if actual != expected:
            raise D8HoldError(
                f"pinned Phase-10 code hash mismatch for {relative}"
            )


def _verify_stage_files(root: Path, bindings: ReserveBindings) -> None:
    build_json = root / "normalized" / bindings.build_id / "build.json"
    if not build_json.is_file():
        raise D8HoldError(f"missing D3 build.json: {build_json}")
    if _sha256_file(build_json) != bindings.build_json_sha256:
        raise D8HoldError("D3 build.json hash mismatch")

    identity_json = (
        root / "derived" / "identity" / bindings.identity_id / "identity.json"
    )
    if not identity_json.is_file():
        raise D8HoldError(f"missing D4 identity.json: {identity_json}")
    if _sha256_file(identity_json) != bindings.identity_json_sha256:
        raise D8HoldError("D4 identity.json hash mismatch")

    assignment = (
        root
        / "derived"
        / "identity"
        / bindings.identity_id
        / "identity_assignment.parquet"
    )
    if not assignment.is_file():
        raise D8HoldError(f"missing D4 identity_assignment.parquet: {assignment}")
    if _sha256_file(assignment) != bindings.identity_assignment_sha256:
        raise D8HoldError("D4 identity_assignment.parquet hash mismatch")

    for component, expected in bindings.reserve_files.items():
        path = _reserve_path(root, bindings, component)
        if not path.is_file():
            raise D8HoldError(f"missing reserve file: {path}")
        if _sha256_file(path) != expected:
            raise D8HoldError(
                f"reserve file {component!r} hash mismatch"
            )


def _verify_schema(root: Path, bindings: ReserveBindings) -> None:
    path = _reserve_path(root, bindings, "observations")
    names = tuple(pq.read_schema(path).names)
    if names != tuple(OBSERVATION_COLUMNS):
        raise D8HoldError(
            "reserve observations schema does not equal the frozen 16 names"
        )


# ---------------------------------------------------------------------------
# The one authorized reserve read
# ---------------------------------------------------------------------------


def _read_keys(
    root: Path, bindings: ReserveBindings
) -> tuple[set[tuple[str, date]], set[tuple[str, date]], set[tuple[str, date]]]:
    observations = pq.read_table(
        _reserve_path(root, bindings, "observations"),
        columns=list(_PROJECTED_COLUMNS["observations"]),
    ).to_pandas()
    obs_keys: set[tuple[str, date]] = set()
    for record in observations.itertuples(index=False):
        row_date = _as_date_cell(record.date)
        if row_date is None:
            raise D8HoldError("reserve observations carries a null date")
        obs_keys.add((str(record.ticker), row_date))

    flags = pq.read_table(
        _reserve_path(root, bindings, "obs_flags"),
        columns=list(_PROJECTED_COLUMNS["obs_flags"]),
    ).to_pandas()
    flag_keys: set[tuple[str, date]] = set()
    for record in flags.itertuples(index=False):
        row_date = _as_date_cell(record.date)
        if row_date is None:
            raise D8HoldError("reserve obs_flags carries a null date")
        flag_keys.add((str(record.ticker), row_date))

    ca_events = pq.read_table(
        _reserve_path(root, bindings, "ca_events"),
        columns=list(_PROJECTED_COLUMNS["ca_events"]),
    ).to_pandas()
    ca_keys: set[tuple[str, date]] = set()
    for record in ca_events.itertuples(index=False):
        row_date = _as_date_cell(record.effective_date)
        if row_date is None:
            raise D8HoldError("reserve ca_events carries a null effective_date")
        ca_keys.add((str(record.stock_id), row_date))

    return obs_keys, flag_keys, ca_keys


def _resolve_security_map(
    root: Path, bindings: ReserveBindings
) -> tuple[dict[str, str], list[tuple[str, str, date, date]]]:
    """Return ``(security_map, eligible_rows)``.

    ``eligible_rows`` are the unique ``INTERVAL_ASSIGNED`` D4 rows; a key that
    appears more than once (whatever its state) is excluded, so a duplicated
    key never maps.
    """
    assignment = (
        root
        / "derived"
        / "identity"
        / bindings.identity_id
        / "identity_assignment.parquet"
    )
    frame = pq.read_table(
        assignment,
        columns=[
            "security_key",
            "ticker",
            "listed_start",
            "listed_end",
            "state",
        ],
    ).to_pandas()

    counts: dict[str, int] = {}
    for key in frame["security_key"]:
        counts[str(key)] = counts.get(str(key), 0) + 1

    eligible: list[tuple[str, str, date, date]] = []
    for record in frame.itertuples(index=False):
        if str(record.state) != "INTERVAL_ASSIGNED":
            continue
        key = str(record.security_key)
        if counts.get(key, 0) != 1:
            continue
        start = _as_date_cell(record.listed_start)
        end = _as_date_cell(record.listed_end)
        if start is None or end is None or start > end:
            raise D8HoldError(
                f"INTERVAL_ASSIGNED key {key!r} has an invalid listed interval"
            )
        eligible.append((key, str(record.ticker), start, end))

    eligible.sort()
    security_map = {key: f"SEC:US:{key}" for key, _, _, _ in eligible}
    return security_map, eligible


def _resolve_subjects(
    obs_keys: set[tuple[str, date]],
    eligible: Sequence[tuple[str, str, date, date]],
) -> list[tuple[str, date]]:
    # Index the eligible D4 rows by ticker so each reserve key is compared only
    # against its own ticker's intervals.  Exhaustively scanning the whole
    # eligible list per key is O(observations x eligible) and cannot finish on
    # the real reserve package; the semantics are unchanged because a key still
    # matches only when exactly one of its ticker's intervals contains the date.
    by_ticker: dict[str, list[tuple[str, date, date]]] = {}
    for key, key_ticker, start, end in eligible:
        by_ticker.setdefault(key_ticker, []).append((key, start, end))
    rows: list[tuple[str, date]] = []
    for ticker, row_date in sorted(obs_keys, key=lambda item: (item[0], item[1])):
        matches = [
            key
            for key, start, end in by_ticker.get(ticker, ())
            if start <= row_date <= end
        ]
        subject = matches[0] if len(matches) == 1 else ticker
        rows.append((subject, row_date))
    return rows


def _certified_calendar() -> Any:
    return build_nyse_calendar(date(2015, 1, 2), date(2027, 12, 31))


def _build_footprint(
    frame: _SchemaBackedKeyFrame,
    security_map: Mapping[str, str],
    calendar: Any,
) -> Footprint:
    return footprint_from_panel(
        frame,
        {"subject": "subject", "date": "date"},
        VARIABLE_MAP,
        security_map,
        {},
        calendar,
    )


def _count_unresolved(unresolved: Sequence[str]) -> dict[str, int]:
    counts = {
        "unmapped_subject": 0,
        "unmapped_column": 0,
        "invalid_date": 0,
        "other": 0,
    }
    for reason in unresolved:
        if reason.startswith("unmapped_subject"):
            counts["unmapped_subject"] += 1
        elif reason.startswith("unmapped_column"):
            counts["unmapped_column"] += 1
        elif reason.startswith("invalid_date"):
            counts["invalid_date"] += 1
        else:
            counts["other"] += 1
    return counts


# ---------------------------------------------------------------------------
# available_from
# ---------------------------------------------------------------------------


def _verify_manifests(root: Path, bindings: ReserveBindings) -> None:
    manifest_root = root / "manifest"
    expected = set(bindings.manifests)
    actual = (
        {path.name for path in manifest_root.iterdir() if path.is_dir()}
        if manifest_root.is_dir()
        else set()
    )
    if actual != expected:
        raise D8HoldError(
            "the manifest batch set is not exactly the two bound batches"
        )
    for batch, files in bindings.manifests.items():
        for filename in _MANIFEST_FILES:
            path = manifest_root / batch / filename
            if not path.is_file():
                raise D8HoldError(f"missing manifest file: {path}")
            expected_hash = files.get(filename)
            if expected_hash is None:
                raise D8HoldError(
                    f"no bound hash for manifest file {batch}/{filename}"
                )
            if _sha256_file(path) != expected_hash:
                raise D8HoldError(
                    f"manifest hash mismatch for {batch}/{filename}"
                )


def _available_from(
    root: Path,
    bindings: ReserveBindings,
    obs_tickers: set[str],
) -> dict[str, Any]:
    qualifying: list[tuple[str, str, str, int, str]] = []
    for batch in sorted(bindings.manifests):
        attempts_path = root / "manifest" / batch / "attempts.jsonl"
        for line in attempts_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = __import__("json").loads(line)
            if not isinstance(record, dict):
                raise D8HoldError("attempts.jsonl line is not an object")
            if record.get("http_status") != 200:
                continue
            if record.get("error_class") is not None:
                continue
            row_count = record.get("row_count")
            if not isinstance(row_count, int) or row_count <= 0:
                continue
            ticker = record.get("ticker")
            if ticker not in obs_tickers:
                continue
            last_date = record.get("last_date")
            if not isinstance(last_date, str) or last_date[:10] < _RESERVE_START_ISO:
                continue
            retrieved = record.get("retrieved_at_utc")
            if not isinstance(retrieved, str) or len(retrieved) < 10:
                continue
            attempt = record.get("attempt")
            if not isinstance(attempt, int):
                attempt = int(attempt) if str(attempt).isdigit() else 0
            qualifying.append(
                (retrieved[:10], batch, str(ticker), attempt, retrieved)
            )

    if not qualifying:
        raise D8HoldError(
            "no acquisition attempt qualifies for available_from"
        )
    qualifying.sort(key=lambda item: (item[0], item[1], item[2], item[3]))
    day, batch, ticker, attempt, retrieved = qualifying[0]
    return {
        "available_from": day,
        "contributing_batch": batch,
        "ticker": ticker,
        "attempt": attempt,
        "retrieved_at_utc": retrieved,
        "manifests": {
            batch_id: dict(files)
            for batch_id, files in sorted(bindings.manifests.items())
        },
    }


# ---------------------------------------------------------------------------
# Payload
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Artifact:
    payload: dict[str, Any]
    footprint: Footprint
    packaging_hash: str
    available_from: str
    determinable: bool
    unresolved_counts: dict[str, int]


def _packaging_hash(bindings: ReserveBindings) -> str:
    mapping = {
        f"reserve_sealed/{bindings.build_id}/{component}.parquet": digest
        for component, digest in bindings.reserve_files.items()
    }
    return content_hash(mapping)


def _build_artifact(root: Path, bindings: ReserveBindings) -> _Artifact:
    root = Path(root)
    _verify_code_hashes(bindings)
    _verify_stage_files(root, bindings)
    _verify_schema(root, bindings)

    _verify_manifests(root, bindings)
    obs_keys, flag_keys, ca_keys = _read_keys(root, bindings)
    if not flag_keys.issubset(obs_keys):
        raise D8HoldError("obs_flags key domain is not a subset of observations")
    if not ca_keys.issubset(obs_keys):
        raise D8HoldError("ca_events key domain is not a subset of observations")

    security_map, eligible = _resolve_security_map(root, bindings)
    resolved = _resolve_subjects(obs_keys, eligible)
    frame = _SchemaBackedKeyFrame(OBSERVATION_COLUMNS, resolved)
    calendar = _certified_calendar()
    footprint = _build_footprint(frame, security_map, calendar)

    available = _available_from(root, bindings, {ticker for ticker, _ in obs_keys})

    unresolved_counts = _count_unresolved(footprint.unresolved)
    calendar_hash = content_hash(
        [timestamp.date().isoformat() for timestamp in calendar.dates]
    )
    packaging_hash = _packaging_hash(bindings)
    code_sha256 = _sha256_file(Path(__file__))
    spec_sha256 = bindings.spec_sha256 or _sha256_file(
        _REPO_ROOT
        / "worker_tasks"
        / "dataset_v1"
        / "task-d8-reserve-firewall.md"
    )
    clarification_sha256 = bindings.clarification_sha256 or _sha256_file(
        _REPO_ROOT / "docs" / "discovery_dataset_v1_1_d8_clarification.md"
    )

    payload: dict[str, Any] = {
        "packaging_hash": packaging_hash,
        "sealed": True,
        "available_from": available["available_from"],
        "source_label": SOURCE_LABEL,
        "d8_sealed_semantics": _SEALED_SEMANTICS,
        "d8_package": {
            "build_id": bindings.build_id,
            "normalized_hash": bindings.normalized_hash,
            "reserve_files": {
                component: digest
                for component, digest in sorted(bindings.reserve_files.items())
            },
        },
        "d8_footprint_read": {
            "purpose": "compute the ARTIFACT footprint only",
            "projected_columns": {
                component: list(columns)
                for component, columns in sorted(_PROJECTED_COLUMNS.items())
            },
            "build_id": bindings.build_id,
            "code_sha256": code_sha256,
            "spec_sha256": spec_sha256,
            "clarification_sha256": clarification_sha256,
            "time_source": "envelope.recorded_at",
        },
        "d8_maps": {
            "security_map_hash": content_hash(dict(security_map)),
            "variable_map_hash": content_hash(dict(VARIABLE_MAP)),
            "market_series_map_hash": content_hash({}),
            "calendar_hash": calendar_hash,
            "derivation_rules_version": DERIVATION_RULES_VERSION,
            "identity_hash": bindings.identity_hash,
            "unresolved_counts": dict(unresolved_counts),
            "unresolved_reasons": list(footprint.unresolved),
        },
        "d8_available_from_evidence": {
            "contributing_batch": available["contributing_batch"],
            "ticker": available["ticker"],
            "attempt": available["attempt"],
            "retrieved_at_utc": available["retrieved_at_utc"],
            "manifests": available["manifests"],
        },
        "d8_historical_disclosure": {
            "rows": [
                [stage, interaction]
                for stage, interaction in _HISTORICAL_DISCLOSURE
            ],
            "note": "not exhaustive",
        },
        "d8_evidence_role": _EVIDENCE_ROLE,
    }

    return _Artifact(
        payload=payload,
        footprint=footprint,
        packaging_hash=packaging_hash,
        available_from=available["available_from"],
        determinable=footprint.determinable,
        unresolved_counts=unresolved_counts,
    )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


class RegistrationMode:
    """The three frozen registration modes (task spec section 6)."""

    TEST = "TEST"
    DRY_RUN = "DRY_RUN"
    PRODUCTION = "PRODUCTION"


_MODE_ALIASES = {
    "TEST": RegistrationMode.TEST,
    "DRY_RUN": RegistrationMode.DRY_RUN,
    "DRY-RUN": RegistrationMode.DRY_RUN,
    "dry-run": RegistrationMode.DRY_RUN,
    "dry_run": RegistrationMode.DRY_RUN,
    "PRODUCTION": RegistrationMode.PRODUCTION,
    "production": RegistrationMode.PRODUCTION,
}


@dataclass(frozen=True)
class RegistrationResult:
    """The result of one registration attempt."""

    mode: str
    created: bool
    record: KnowledgeRecord
    record_hash: str
    seq: int
    determinable: bool
    unresolved_counts: Mapping[str, int]
    packaging_hash: str
    available_from: str
    log_path: str


def _coerce_mode(mode: Any) -> str:
    try:
        return _MODE_ALIASES[str(mode)]
    except KeyError as exc:
        raise D8RegistrationModeError(f"unknown registration mode {mode!r}") from exc


def _same_artifact(
    existing: KnowledgeRecord, artifact: _Artifact
) -> bool:
    if existing.footprint is None:
        return False
    return (
        content_hash(existing.payload) == content_hash(artifact.payload)
        and content_hash(existing.footprint)
        == content_hash(artifact.footprint.body)
    )


def _result(
    mode: str,
    created: bool,
    record: KnowledgeRecord,
    artifact: _Artifact,
    log_path: Path,
) -> RegistrationResult:
    return RegistrationResult(
        mode=mode,
        created=created,
        record=record,
        record_hash=record.record_hash,
        seq=record.seq,
        determinable=artifact.determinable,
        unresolved_counts=dict(artifact.unresolved_counts),
        packaging_hash=artifact.packaging_hash,
        available_from=artifact.available_from,
        log_path=str(log_path),
    )


def _resolve_target(
    root: Path,
    log_path: str | Path,
    mode: str,
    authorization: str | None,
) -> Path:
    production = _production_log_path(root)
    resolved = Path(log_path).resolve()
    if mode == RegistrationMode.TEST:
        if resolved == production.resolve():
            raise D8RegistrationModeError(
                "TEST mode refuses the production Knowledge-PIT path"
            )
        return Path(log_path)
    if mode == RegistrationMode.DRY_RUN:
        tmp_dir = Path(tempfile.mkdtemp(prefix="d8-dry-run-"))
        target = tmp_dir / "knowledge.jsonl"
        if production.is_file():
            shutil.copyfile(production, target)
        return target
    if mode == RegistrationMode.PRODUCTION:
        if resolved != production.resolve():
            raise D8RegistrationModeError(
                "PRODUCTION mode requires the production Knowledge-PIT path"
            )
        if authorization != PRODUCTION_AUTHORIZATION:
            raise D8RegistrationModeError(
                "PRODUCTION mode requires the exact authorization token"
            )
        return Path(log_path)
    raise D8RegistrationModeError(f"unknown registration mode {mode!r}")


def register_reserve_artifact(
    root: str | Path,
    log_path: str | Path,
    *,
    mode: Any,
    authorization: str | None = None,
    bindings: ReserveBindings | None = None,
) -> RegistrationResult:
    """Verify the reserve package and register its sealed ``ARTIFACT``.

    The log is read under :meth:`KnowledgeLog.locked` with
    ``blocking=False`` (a held lock surfaces immediately).  A truncated tail
    or an integrity error is raised and never repaired.  An identical existing
    artifact is a byte-preserving no-op; any other same-``source_label``
    artifact is a :class:`D8ConflictError`; a log with any unrelated record is
    a :class:`D8HoldError`.
    """
    root = Path(root)
    effective_bindings = bindings if bindings is not None else default_bindings()
    registration_mode = _coerce_mode(mode)
    target = _resolve_target(
        root, log_path, registration_mode, authorization
    )

    artifact = _build_artifact(root, effective_bindings)

    log = KnowledgeLog(target)
    with log.locked(blocking=False):
        records = read_records(target)
        existing = None
        for record in records:
            if (
                record.kind == RecordKind.ARTIFACT
                and record.payload.get("source_label") == SOURCE_LABEL
            ):
                existing = record
                break
        if existing is not None:
            if _same_artifact(existing, artifact):
                return _result(
                    registration_mode, False, existing, artifact, target
                )
            raise D8ConflictError(
                "a different ARTIFACT already carries the same source_label"
            )
        if records:
            raise D8HoldError(
                "the Knowledge-PIT log already holds unrelated records"
            )

    record = log.append(
        kind=RecordKind.ARTIFACT,
        channel=Channel.SYSTEM,
        program_id=None,
        refs={},
        footprint=artifact.footprint.body,
        payload=artifact.payload,
        event_time=None,
        blocking=False,
    )
    if record.seq != 0 or record.prev_hash != GENESIS_PREV_HASH:
        raise D8HoldError(
            "the appended genesis ARTIFACT is not at seq 0 with a zero prev_hash"
        )
    return _result(registration_mode, True, record, artifact, target)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="reserve_artifact")
    parser.add_argument("--root", required=True)
    parser.add_argument("--mode", choices=["dry-run", "production"], required=True)
    parser.add_argument("--authorization", default=None)
    args = parser.parse_args(argv)

    root = Path(args.root)
    if args.mode == "dry-run":
        result = register_reserve_artifact(
            root,
            _production_log_path(root),
            mode=RegistrationMode.DRY_RUN,
        )
    else:
        result = register_reserve_artifact(
            root,
            _production_log_path(root),
            mode=RegistrationMode.PRODUCTION,
            authorization=args.authorization,
        )

    print(f"mode={result.mode}")
    print(f"record_hash={result.record_hash}")
    print(f"seq={result.seq}")
    print(f"determinable={result.determinable}")
    print(f"unresolved_counts={result.unresolved_counts}")
    print(f"packaging_hash={result.packaging_hash}")
    print(f"available_from={result.available_from}")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised via main()
    raise SystemExit(main())
