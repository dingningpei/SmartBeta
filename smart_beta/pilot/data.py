"""Pilot 1A P1A-G1: the Gate-B -> ``TrustedInput`` PIT input adapter.

This module owns **only** the frozen PIT input adapter of the Pilot-1A
harness introduced by ``worker_tasks/pilot1/pilot1-plan.md`` section 10 (the
P1A-G1 row of section 18's task table). It composes the sealed Phase-6/3/4B
authorities -- never redefining them -- to turn the committed, offline
Phase-5A Gate-B fixtures into the single admitted ``LIVE_RECORDED``
``daily_total_return`` input:

    fixture directory (hash-verified)
        -> replay_transport(TiingoClient)
        -> TiingoPITSource
        -> PointInTimeView.as_of(...).adjusted_returns(...)
        -> TrustedInput (date x stock value frame + DataCapability)
        + realized-return panel ``(date, stock_id, adj_ret)``
        + DataProvenance record

Frozen surface (plan section 10)
--------------------------------

* :func:`compute_fixture_hashes` / :func:`verify_fixture_hashes` -- the
  per-file SHA-256 manifest helper (computed once at config freeze; verified
  before any parsing, with no git needed at run time);
* :data:`DAILY_TOTAL_RETURN` / :data:`DAILY_TOTAL_RETURN_REQUIREMENT` -- the
  one frozen, vendor-free semantic input;
* :data:`GATE_B_UNIVERSE` -- the frozen 26-name Gate-B universe;
* :func:`load_pit_inputs` -- the adapter returning :class:`PilotData`;
* :func:`assert_frozen_claim` -- the machine-checkable claim boundary that
  rejects a deliberately over-claiming input;
* :class:`PilotDataProvenance` / :class:`PilotData`.

Binding constraints (plan sections 10, 19 and 20)
-------------------------------------------------

* No network call, no provider/model SDK, no credential -- the fixtures are
  replayed through the existing :func:`replay_transport` only.
* The adapter never claims ``has_positive_vintage_identity=True`` and never
  claims an evidence class above :attr:`EvidenceClass.LIVE_RECORDED`; a
  deliberately over-claiming double fails closed via
  :func:`assert_frozen_claim`.
* Certification is **not** decided here: this adapter produces the
  :class:`~smart_beta.spec.engine.TrustedInput`; the sealed Phase-6
  ``admit``/``evaluate_factor`` boundary decides admission.
* No forward/back-fill is ever performed: every ``(date, stock)`` cell must
  be present in the trusted path's own output, and a missing cell fails
  closed rather than being interpolated.
* No ``marketCap``/fundamentals field is read. The adapter calls only the
  EOD-price-derived raw-return and corporate-action paths.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from smart_beta.pilot.contracts import content_hash
from smart_beta.pit.schema import ADJUSTED_RETURN_COL, DATE_COL, STOCK_COL
from smart_beta.pit.view import PointInTimeView
from smart_beta.spec.engine import EvidenceClass, TrustedInput
from smart_beta.spec.requirements import (
    DataCapability,
    DataRequirement,
    Frequency,
    ObservationPeriod,
    RevisionPolicy,
    Unit,
)
from smart_beta.vendors.tiingo.client import TiingoClient, replay_transport
from smart_beta.vendors.tiingo.identifiers import resolve_stock_id
from smart_beta.vendors.tiingo.source import TiingoPITSource

__all__ = [
    # fail-closed errors
    "PilotDataError",
    "FixtureIntegrityError",
    "FixtureFormatError",
    "ObservationError",
    "ClaimBoundaryError",
    # frozen semantic input
    "DAILY_TOTAL_RETURN",
    "DAILY_TOTAL_RETURN_REQUIREMENT",
    "KNOWLEDGE_DATE_RULE",
    "MAX_EVIDENCE_CLASS",
    "GATE_B_UNIVERSE",
    # fixture integrity
    "FixtureDigest",
    "compute_fixture_hashes",
    "verify_fixture_hashes",
    # adapter surface
    "assert_frozen_claim",
    "PilotDataProvenance",
    "PilotData",
    "load_pit_inputs",
]

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

#: A git object id: SHA-1 (40 hex) for this repository, SHA-256 (64 hex) tolerated.
_GIT_OBJECT_ID_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")

#: The one frozen semantic input (plan section 5): daily total return.
DAILY_TOTAL_RETURN = "daily_total_return"

#: The frozen Phase-6 requirement G1 satisfies (plan section 10). Vendor-free,
#: daily, a period quantity in fraction units, PIT-admissible, with **no**
#: positive vintage-identity requirement -- G1 never claims one.
DAILY_TOTAL_RETURN_REQUIREMENT = DataRequirement(
    semantic_id=DAILY_TOTAL_RETURN,
    frequency=Frequency.DAILY,
    observation_period=ObservationPeriod.PERIOD,
    units=Unit.FRACTION,
    lookback=0,
    revision_policy=RevisionPolicy.POINT_IN_TIME,
    require_knowledge_date=True,
    require_positive_vintage_identity=False,
)

#: The frozen knowledge-date rule for market data (plan section 5). Cited to
#: Phase 4B and the sealed bitemporal decision table in
#: :mod:`smart_beta.pit.schema`: market data has no separate knowledge-time
#: dimension, so each return's knowledge date is its own trading date.
KNOWLEDGE_DATE_RULE = (
    "knowledge_date = the return's own trading date (end-of-day); market "
    "data carries no separate knowledge-time dimension "
    "(smart_beta/pit/schema.py bitemporal decision table), and the Tiingo "
    "EOD raw-return, corporate-action-adjustment and "
    "survivorship-through-view statuses are PASS in "
    "docs/phase4b_tiingo_certification.md"
)

#: The strongest evidence class G1 may ever claim (plan section 10).
MAX_EVIDENCE_CLASS = EvidenceClass.LIVE_RECORDED

#: The frozen 26-name Gate-B universe selected from the dated DJIA
#: constituent snapshot (plan section 5). It is **not** survivorship-safe;
#: that limitation is carried into every claim and never hidden here.
GATE_B_UNIVERSE: tuple[str, ...] = (
    "MMM",
    "AXP",
    "AMGN",
    "AAPL",
    "BA",
    "CAT",
    "CVX",
    "CSCO",
    "KO",
    "DIS",
    "GS",
    "HD",
    "HON",
    "IBM",
    "JNJ",
    "JPM",
    "MCD",
    "MRK",
    "MSFT",
    "NKE",
    "PG",
    "CRM",
    "TRV",
    "UNH",
    "V",
    "WMT",
)


# ---------------------------------------------------------------------------
# fail-closed errors
# ---------------------------------------------------------------------------


class PilotDataError(ValueError):
    """Base class for every P1A-G1 input-adapter violation."""


class FixtureIntegrityError(PilotDataError):
    """A fixture file is missing, extra or modified relative to the frozen list."""


class FixtureFormatError(PilotDataError):
    """A hash-verified fixture/manifest is malformed and cannot be parsed."""


class ObservationError(PilotDataError):
    """A missing/duplicate/non-finite/out-of-coverage observation was found."""


class ClaimBoundaryError(PilotDataError):
    """An input claims provenance/vintage evidence above the frozen boundary."""


# ---------------------------------------------------------------------------
# fixture integrity (per-file SHA-256 list; verified before parsing)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FixtureDigest:
    """One fixture file's relative path and SHA-256 (frozen at config freeze)."""

    path: str
    sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.path, str) or not self.path:
            raise FixtureIntegrityError(
                f"fixture digest path must be a non-empty string, got {self.path!r}"
            )
        if not isinstance(self.sha256, str) or not _SHA256_RE.match(self.sha256):
            raise FixtureIntegrityError(
                f"fixture digest for {self.path!r} must be a 64-char lowercase "
                f"hex SHA-256, got {self.sha256!r}"
            )

    def to_dict(self) -> dict[str, str]:
        return {"path": self.path, "sha256": self.sha256}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def compute_fixture_hashes(fixture_dir: str | Path) -> tuple[FixtureDigest, ...]:
    """Compute the deterministic per-file SHA-256 list of a fixture directory.

    Used once at config freeze to produce the frozen list (plan section 5).
    Every regular file under ``fixture_dir`` is included, keyed by its
    POSIX-style path relative to the directory, in sorted order. The
    ``manifest.json`` file is included like any other (the frozen Gate-B
    directory has 79 files: 78 recordings + the manifest).
    """
    root = Path(fixture_dir)
    if not root.is_dir():
        raise FixtureIntegrityError(f"fixture directory does not exist: {root}")
    files = sorted(
        (path for path in root.rglob("*") if path.is_file()),
        key=lambda path: path.relative_to(root).as_posix(),
    )
    return tuple(
        FixtureDigest(
            path=path.relative_to(root).as_posix(),
            sha256=_sha256_file(path),
        )
        for path in files
    )


def _normalize_expected_hashes(
    expected: (
        Mapping[str, str]
        | Sequence[FixtureDigest]
        | Sequence[tuple[str, str]]
        | None
    ),
) -> dict[str, str]:
    """Normalize a supplied frozen list to ``{relative_path: sha256}``."""
    if expected is None:
        raise FixtureIntegrityError("a frozen per-file SHA-256 list is required")
    items = expected.items() if isinstance(expected, Mapping) else expected
    normalized: dict[str, str] = {}
    for entry in items:
        if isinstance(entry, FixtureDigest):
            path, digest = entry.path, entry.sha256
        elif isinstance(entry, (tuple, list)) and len(entry) == 2:
            path, digest = entry
        else:
            raise FixtureIntegrityError(
                f"frozen hash entries must be FixtureDigest or (path, sha256) "
                f"pairs, got {entry!r}"
            )
        if not isinstance(path, str) or not path:
            raise FixtureIntegrityError(
                f"frozen hash path must be a non-empty string, got {path!r}"
            )
        if not isinstance(digest, str) or not _SHA256_RE.match(digest):
            raise FixtureIntegrityError(
                f"frozen hash for {path!r} must be a 64-char lowercase hex "
                f"SHA-256, got {digest!r}"
            )
        if path in normalized:
            raise FixtureIntegrityError(f"duplicate frozen hash entry for {path!r}")
        normalized[path] = digest
    if not normalized:
        raise FixtureIntegrityError("the frozen per-file SHA-256 list is empty")
    return normalized


def verify_fixture_hashes(
    fixture_dir: str | Path,
    expected: (
        Mapping[str, str]
        | Sequence[FixtureDigest]
        | Sequence[tuple[str, str]]
        | None
    ),
) -> tuple[FixtureDigest, ...]:
    """Fail closed unless every fixture file matches the frozen SHA-256 list.

    Runs **before any parsing** (plan section 10). A missing file, an extra
    file or a single modified byte raises :class:`FixtureIntegrityError`,
    naming every offending path. No git invocation is required.
    """
    expected_map = _normalize_expected_hashes(expected)
    actual = compute_fixture_hashes(fixture_dir)
    actual_map = {digest.path: digest.sha256 for digest in actual}

    missing = sorted(set(expected_map) - set(actual_map))
    extra = sorted(set(actual_map) - set(expected_map))
    modified = sorted(
        path
        for path in set(expected_map) & set(actual_map)
        if expected_map[path] != actual_map[path]
    )
    if missing or extra or modified:
        raise FixtureIntegrityError(
            "fixture integrity check failed: "
            f"missing={missing} extra={extra} modified={modified}"
        )
    return actual


# ---------------------------------------------------------------------------
# fixture parsing / date capping
# ---------------------------------------------------------------------------


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise FixtureFormatError(f"cannot parse fixture {path}: {exc}") from exc


def _read_manifest(fixture_dir: Path) -> Mapping[str, Any]:
    manifest_path = fixture_dir / "manifest.json"
    if not manifest_path.is_file():
        raise FixtureFormatError(f"fixture manifest is missing: {manifest_path}")
    manifest = _read_json(manifest_path)
    if not isinstance(manifest, Mapping):
        raise FixtureFormatError("fixture manifest must be a JSON object")
    recordings = manifest.get("recordings")
    if not isinstance(recordings, Mapping) or not recordings:
        raise FixtureFormatError(
            "fixture manifest must carry a non-empty 'recordings' mapping"
        )
    return manifest


def _row_date_string(row: Mapping[str, Any]) -> str:
    """The ``YYYY-MM-DD`` prefix of a raw fixture row's ``date`` field."""
    value = row.get("date")
    if not isinstance(value, str) or len(value) < 10:
        raise FixtureFormatError(
            f"fixture row is missing a usable 'date' field: {value!r}"
        )
    return value[:10]


def _build_recordings(
    fixture_dir: Path,
    manifest: Mapping[str, Any],
    date_cap: str | None,
) -> tuple[dict[str, tuple[int, Any]], str, str]:
    """Parse every recording, cap EOD rows, and return ``(recordings, start, end)``.

    The date cap is enforced here, at the raw-body boundary: a row dated at
    or after the cap is removed *before* the body is handed to the trusted
    PIT source, so no capped-out row can ever reach
    :class:`TiingoPITSource`/``PointInTimeView`` (plan sections 5 and 10).
    Coverage is computed from the uncapped EOD bodies.
    """
    recordings: dict[str, tuple[int, Any]] = {}
    coverage_start: str | None = None
    coverage_end: str | None = None

    for filename, entry in manifest["recordings"].items():
        if not isinstance(entry, Mapping):
            raise FixtureFormatError(f"manifest recording {filename!r} is malformed")
        url_path = entry.get("url_path")
        status_code = entry.get("status_code", 200)
        if not isinstance(url_path, str) or not url_path:
            raise FixtureFormatError(
                f"manifest recording {filename!r} has no usable url_path"
            )
        if url_path in recordings:
            raise FixtureFormatError(
                f"manifest maps more than one recording to {url_path!r}"
            )
        body = _read_json(fixture_dir / str(filename))

        if url_path.endswith("/prices"):
            if not isinstance(body, list):
                raise FixtureFormatError(
                    f"EOD recording {filename!r} must be a JSON list"
                )
            row_dates = [_row_date_string(row) for row in body]
            if row_dates:
                row_min, row_max = min(row_dates), max(row_dates)
                coverage_start = (
                    row_min if coverage_start is None else min(coverage_start, row_min)
                )
                coverage_end = (
                    row_max if coverage_end is None else max(coverage_end, row_max)
                )
            if date_cap is not None:
                body = [row for row in body if _row_date_string(row) < date_cap]

        recordings[url_path] = (int(status_code), body)

    if coverage_start is None or coverage_end is None:
        raise FixtureFormatError(
            "no EOD /prices recording found; the fixture directory cannot "
            "supply daily_total_return"
        )
    return recordings, coverage_start, coverage_end


# ---------------------------------------------------------------------------
# claim boundary
# ---------------------------------------------------------------------------


def assert_frozen_claim(trusted_input: TrustedInput) -> TrustedInput:
    """Fail closed on any claim above the frozen G1 provenance boundary.

    The frozen boundary (plan section 10) is: evidence class at most
    :data:`MAX_EVIDENCE_CLASS` (``LIVE_RECORDED``), **no** positive
    vintage-identity claim, **no** vintage evidence, and exactly the
    :data:`DAILY_TOTAL_RETURN` semantic. A deliberately over-claiming input
    -- the test suite's double -- fails here rather than being silently
    accepted.
    """
    if not isinstance(trusted_input, TrustedInput):
        raise ClaimBoundaryError(
            f"expected a TrustedInput, got {type(trusted_input).__name__}"
        )
    if trusted_input.evidence_class.rank > MAX_EVIDENCE_CLASS.rank:
        raise ClaimBoundaryError(
            "G1 evidence class "
            f"{trusted_input.evidence_class.value!r} exceeds the frozen "
            f"maximum {MAX_EVIDENCE_CLASS.value!r}"
        )
    if trusted_input.vintage_evidence is not None:
        raise ClaimBoundaryError(
            "G1 must not supply positive vintage evidence; the frozen "
            "requirement does not ask for it"
        )
    capability = trusted_input.capability
    if capability.has_positive_vintage_identity:
        raise ClaimBoundaryError(
            "G1 must not claim has_positive_vintage_identity=True"
        )
    if capability.semantic_id != DAILY_TOTAL_RETURN:
        raise ClaimBoundaryError(
            f"G1 must claim exactly {DAILY_TOTAL_RETURN!r}, got "
            f"{capability.semantic_id!r}"
        )
    return trusted_input


def _validate_requirement(requirement: DataRequirement) -> DataRequirement:
    """Reject any request that is not the frozen ``daily_total_return`` input."""
    if not isinstance(requirement, DataRequirement):
        raise PilotDataError(
            f"requirement must be a DataRequirement, got {type(requirement).__name__}"
        )
    if requirement.semantic_id != DAILY_TOTAL_RETURN:
        raise PilotDataError(
            "P1A-G1 admits only the frozen semantic input "
            f"{DAILY_TOTAL_RETURN!r}; got {requirement.semantic_id!r}. "
            "marketCap, fundamentals and every non-frozen field are excluded "
            "(plan section 5)."
        )
    return requirement


# ---------------------------------------------------------------------------
# provenance
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PilotDataProvenance:
    """The frozen, JSON-safe G1 provenance record (plan section 10).

    Carries the fixture tree id, the verified per-file SHA-256 list, the
    universe, the requested range, the optional dry-run date cap, the
    resolved fixture coverage, the frozen knowledge-date rule, the admitted
    semantic and evidence class, and the realized observation count.
    ``requirement`` is the exact frozen :class:`DataRequirement` payload.
    """

    fixture_tree_id: str
    file_hashes: tuple[FixtureDigest, ...]
    universe: tuple[str, ...]
    start: str
    end: str
    date_cap: str | None
    knowledge_date_rule: str
    semantic_id: str
    evidence_class: str
    observation_count: int
    coverage_start: str
    coverage_end: str
    max_observation_date: str
    requirement: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "fixture_tree_id": self.fixture_tree_id,
            "file_hashes": [digest.to_dict() for digest in self.file_hashes],
            "file_hash_count": len(self.file_hashes),
            "universe": list(self.universe),
            "universe_size": len(self.universe),
            "start": self.start,
            "end": self.end,
            "date_cap": self.date_cap,
            "coverage_start": self.coverage_start,
            "coverage_end": self.coverage_end,
            "max_observation_date": self.max_observation_date,
            "observation_count": self.observation_count,
            "semantic_id": self.semantic_id,
            "evidence_class": self.evidence_class,
            "knowledge_date_rule": self.knowledge_date_rule,
            "requirement": dict(self.requirement),
        }

    def content_hash(self) -> str:
        return content_hash(self.to_dict())


@dataclass(frozen=True)
class PilotData:
    """The frozen P1A-G1 adapter output (plan section 10).

    Attributes:
        trusted_input: The single ``LIVE_RECORDED`` :class:`TrustedInput`
            for ``daily_total_return`` (a date-indexed, stock-column value
            frame plus its :class:`DataCapability`).
        realized_returns: The ``(date, stock_id, adj_ret)`` long panel Phase 7
            consumes.
        provenance: The :class:`PilotDataProvenance` record.
    """

    trusted_input: TrustedInput
    realized_returns: pd.DataFrame
    provenance: PilotDataProvenance


# ---------------------------------------------------------------------------
# the adapter
# ---------------------------------------------------------------------------


def _validate_date_literal(value: str, *, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ObservationError(f"{field_name} must be a non-empty date string")
    try:
        pd.Timestamp(value)
    except (TypeError, ValueError) as exc:
        raise ObservationError(f"{field_name} is not a usable date: {value!r}") from exc
    return value


def _validate_long_frame(adjusted: pd.DataFrame) -> pd.DataFrame:
    """Validate the trusted long panel, failing closed on any defect."""
    if set(adjusted.columns) != {DATE_COL, STOCK_COL, ADJUSTED_RETURN_COL}:
        raise ObservationError(
            "adjusted-return panel must have exactly columns "
            f"{(DATE_COL, STOCK_COL, ADJUSTED_RETURN_COL)}, got "
            f"{tuple(adjusted.columns)}"
        )
    frame = adjusted.loc[:, [DATE_COL, STOCK_COL, ADJUSTED_RETURN_COL]].copy()
    frame[DATE_COL] = pd.to_datetime(frame[DATE_COL])
    if not pd.api.types.is_float_dtype(frame[ADJUSTED_RETURN_COL]):
        raise ObservationError(
            "adjusted-return values must be floating point, got dtype "
            f"{frame[ADJUSTED_RETURN_COL].dtype}"
        )
    if bool(frame.duplicated(subset=[DATE_COL, STOCK_COL]).any()):
        raise ObservationError(
            "adjusted-return panel has duplicate (date, stock_id) rows"
        )
    values = frame[ADJUSTED_RETURN_COL].to_numpy(dtype=float)
    if not bool(np.isfinite(values).all()):
        bad = int((~np.isfinite(values)).sum())
        raise ObservationError(
            f"adjusted-return panel has {bad} non-finite value(s); no "
            "substitution or fill is performed"
        )
    return frame.sort_values([DATE_COL, STOCK_COL], kind="mergesort").reset_index(
        drop=True
    )


def _meta_body(
    recordings: Mapping[str, tuple[int, Any]], ticker: str
) -> Mapping[str, Any]:
    """The hash-verified daily-metadata body used for the ticker's stock_id."""
    key = f"/tiingo/daily/{ticker}"
    if key not in recordings:
        raise FixtureFormatError(
            f"fixture manifest has no daily metadata recording for {ticker!r}"
        )
    body = recordings[key][1]
    if not isinstance(body, Mapping):
        raise FixtureFormatError(
            f"daily metadata for {ticker!r} must be a JSON object"
        )
    return body


def _realized_to_value_frame(realized: pd.DataFrame) -> pd.DataFrame:
    """Pivot the long panel to the sealed observation-date x stock value frame."""
    wide = realized.pivot(
        index=DATE_COL, columns=STOCK_COL, values=ADJUSTED_RETURN_COL
    )
    wide = wide.astype("float64")
    wide.columns = wide.columns.astype("string")
    wide.index = pd.DatetimeIndex(wide.index)
    return wide.sort_index(axis=0).sort_index(axis=1)


def load_pit_inputs(
    *,
    fixture_dir: str | Path,
    expected_fixture_hashes: (
        Mapping[str, str] | Sequence[FixtureDigest] | Sequence[tuple[str, str]]
    ),
    universe: Sequence[str],
    start: str,
    end: str,
    requirement: DataRequirement,
    fixture_tree_id: str,
    date_cap: str | None = None,
) -> PilotData:
    """Build the frozen ``daily_total_return`` ``TrustedInput`` from fixtures.

    Parameters
    ----------
    fixture_dir:
        Directory of committed Gate-B fixture files (read-only, never
        modified).
    expected_fixture_hashes:
        The frozen per-file SHA-256 list (mapping or ``(path, sha256)``
        sequence). Verified before any parsing.
    universe:
        The frozen ticker universe (26 names for Gates B).
    start, end:
        The requested observation range. Both must lie inside the fixture's
        own EOD coverage, or the adapter fails closed.
    requirement:
        The frozen :class:`DataRequirement`; only ``daily_total_return`` is
        admitted.
    fixture_tree_id:
        The frozen git tree id of the fixture directory (config freeze
        metadata; the adapter does not invoke git).
    date_cap:
        Optional dry-run cap. No row dated at or after it is ever handed to
        the trusted PIT path.

    Returns
    -------
    PilotData
        The ``TrustedInput``, the ``(date, stock_id, adj_ret)`` realized
        panel and the :class:`PilotDataProvenance` record.

    Raises
    ------
    FixtureIntegrityError, FixtureFormatError, ObservationError,
    ClaimBoundaryError
        On any integrity, format, observation or claim-boundary defect.
    """
    requirement = _validate_requirement(requirement)
    if not isinstance(fixture_tree_id, str) or not _GIT_OBJECT_ID_RE.match(
        fixture_tree_id
    ):
        raise PilotDataError(
            "fixture_tree_id must be a lowercase hex git tree object id "
            "(40-char SHA-1 in this repository)"
        )
    if not universe:
        raise PilotDataError("universe must be a non-empty sequence of tickers")
    if len(set(universe)) != len(universe):
        raise PilotDataError("universe must not contain duplicate tickers")

    start = _validate_date_literal(start, field_name="start")
    end = _validate_date_literal(end, field_name="end")
    if date_cap is not None:
        date_cap = _validate_date_literal(date_cap, field_name="date_cap")

    fixture_root = Path(fixture_dir)
    verified_hashes = verify_fixture_hashes(fixture_root, expected_fixture_hashes)

    manifest = _read_manifest(fixture_root)
    recordings, coverage_start, coverage_end = _build_recordings(
        fixture_root, manifest, date_cap
    )

    requested_start = pd.Timestamp(start)
    requested_end = pd.Timestamp(end)
    if requested_start > requested_end:
        raise ObservationError(
            f"requested start {start!r} is after requested end {end!r}"
        )
    if start < coverage_start:
        raise ObservationError(
            f"requested start {start!r} is before fixture coverage "
            f"{coverage_start!r}"
        )
    if end > coverage_end:
        raise ObservationError(
            f"requested end {end!r} is after fixture coverage {coverage_end!r}"
        )

    effective_end = requested_end
    if date_cap is not None:
        cap = pd.Timestamp(date_cap)
        effective_end = min(requested_end, cap)
        if effective_end < requested_start:
            raise ObservationError(
                f"date cap {date_cap!r} leaves no observations at or after "
                f"start {start!r}"
            )

    client = TiingoClient(transport=replay_transport(recordings))
    source = TiingoPITSource(list(universe), client=client)
    view = PointInTimeView(source)
    try:
        adjusted = view.as_of(effective_end).adjusted_returns(
            requested_start, effective_end
        )
    except PilotDataError:
        raise
    except Exception as exc:  # noqa: BLE001 - the trusted boundary fails closed
        # The sealed PIT/schema layers reject a duplicate row, a malformed
        # panel, an unsupported dtype and similar defects before returning.
        # The adapter is the input boundary, so every such defect surfaces
        # as a typed Pilot-1A data error rather than a raw vendor error.
        raise ObservationError(
            f"the trusted PIT path failed closed for the requested window: {exc}"
        ) from exc

    realized = _validate_long_frame(adjusted)
    if realized.empty:
        raise ObservationError("the trusted PIT path returned no observations")

    dates = pd.DatetimeIndex(sorted(realized[DATE_COL].unique()))
    observed_stocks = sorted(str(stock) for stock in realized[STOCK_COL].unique())
    expected_stocks = sorted(
        resolve_stock_id(_meta_body(recordings, ticker)).stock_id
        for ticker in universe
    )
    if observed_stocks != expected_stocks:
        missing_universe = sorted(set(expected_stocks) - set(observed_stocks))
        extra_universe = sorted(set(observed_stocks) - set(expected_stocks))
        raise ObservationError(
            "realized panel universe does not match the frozen universe: "
            f"missing={missing_universe} extra={extra_universe}"
        )

    expected_grid = pd.MultiIndex.from_product([dates, expected_stocks])
    observed_grid = pd.MultiIndex.from_frame(realized[[DATE_COL, STOCK_COL]])
    missing_cells = expected_grid.difference(observed_grid)
    if len(missing_cells):
        raise ObservationError(
            f"realized panel is missing {len(missing_cells)} (date, stock) "
            "observation(s); the adapter never forward/back-fills"
        )

    if dates.min() < requested_start or dates.max() > effective_end:
        raise ObservationError(
            f"realized dates [{dates.min()}, {dates.max()}] fall outside the "
            f"requested range [{start}, {end}]"
        )
    if dates.min() < pd.Timestamp(coverage_start) or dates.max() > pd.Timestamp(
        coverage_end
    ):
        raise ObservationError(
            f"realized dates [{dates.min()}, {dates.max()}] fall outside "
            f"fixture coverage [{coverage_start}, {coverage_end}]"
        )
    if date_cap is not None and dates.max() >= pd.Timestamp(date_cap):
        raise ObservationError(
            f"realized date {dates.max()} is at or after the date cap {date_cap}"
        )

    values = _realized_to_value_frame(realized)
    capability = DataCapability(
        semantic_id=DAILY_TOTAL_RETURN,
        frequency=Frequency.DAILY,
        observation_period=ObservationPeriod.PERIOD,
        units=Unit.FRACTION,
        history=int(len(values.index)),
        has_knowledge_date=True,
        has_positive_vintage_identity=False,
        revision_policies=frozenset({RevisionPolicy.POINT_IN_TIME}),
    )
    trusted_input = TrustedInput(
        values=values,
        capability=capability,
        evidence_class=EvidenceClass.LIVE_RECORDED,
        vintage_evidence=None,
    )
    assert_frozen_claim(trusted_input)

    provenance = PilotDataProvenance(
        fixture_tree_id=fixture_tree_id,
        file_hashes=verified_hashes,
        universe=tuple(universe),
        start=start,
        end=end,
        date_cap=date_cap,
        knowledge_date_rule=KNOWLEDGE_DATE_RULE,
        semantic_id=DAILY_TOTAL_RETURN,
        evidence_class=EvidenceClass.LIVE_RECORDED.value,
        observation_count=int(len(values.index)),
        coverage_start=coverage_start,
        coverage_end=coverage_end,
        max_observation_date=dates.max().strftime("%Y-%m-%d"),
        requirement=requirement.to_dict(),
    )
    return PilotData(
        trusted_input=trusted_input,
        realized_returns=realized,
        provenance=provenance,
    )
