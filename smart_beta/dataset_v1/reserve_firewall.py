"""D8 (i): the prospective Dataset-v1 development accessor.

This module owns **only** the development-access firewall frozen by
``worker_tasks/dataset_v1/task-d8-reserve-firewall.md`` section 2 (authority
order: contract sections 12/14/16-gate-12/20; the D8 clarification; the frozen
Phase-10 plan sections 5.1/5.2/5.4/6.2/6.5/13.1).  It is a *read* API for the
D3 development partition only, and it rejects, before any row is materialized,
every request that touches a session on or after the frozen reserve boundary
``2023-07-03``.

The accessor is deliberately tiny and fail-closed:

* its only data source is the frozen, hash-pinned D3 development files
  ``normalized/n-f874d3ecb61374d4/{observations,obs_flags,ca_events}.parquet``;
* it has **no** path, artifact-id or metadata parameter: the path comes from
  the frozen bindings alone;
* it rejects the reserve path components ``reserve_sealed``, ``raw`` and
  ``dsc_evidence`` and ``..``, and refuses a resolved real path outside the
  development directory (a symlink escape);
* it verifies the bound SHA-256 of the file before reading;
* it reads with pyarrow's ``filters=`` (never read-then-filter) and asserts
  post-load that every returned date lies in ``[start, end]`` and before the
  boundary;
* it never opens a reserve file and never returns reserve metadata.

The Phase-10 code is used read-only.  No provider or network call is made.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

import pandas as pd
import pyarrow.parquet as pq

from smart_beta.dataset_v1.normalize import (
    CA_EVENT_COLUMNS,
    FLAG_COLUMNS,
    OBSERVATION_COLUMNS,
)

__all__ = [
    # constants
    "D3_BUILD_ID",
    "RESERVE_START",
    "OBSERVATION_SCHEMA",
    "FLAG_SCHEMA",
    "CA_EVENT_SCHEMA",
    "FileBinding",
    "default_bindings",
    # errors
    "AccessorError",
    "AccessorInputError",
    "ReserveFirewallError",
    "D8FirewallError",
    # API
    "DevelopmentAccessor",
]


# ---------------------------------------------------------------------------
# Frozen constants
# ---------------------------------------------------------------------------

#: The frozen D3 normalized build id (task spec section 1).
D3_BUILD_ID = "n-f874d3ecb61374d4"

#: The sealed reserve boundary; a session ``>=`` this is never development data.
RESERVE_START = date(2023, 7, 3)

#: The frozen D3 component schemas (task spec section 1 / D3 plan section 3.4).
OBSERVATION_SCHEMA: tuple[str, ...] = tuple(OBSERVATION_COLUMNS)
FLAG_SCHEMA: tuple[str, ...] = tuple(FLAG_COLUMNS)
CA_EVENT_SCHEMA: tuple[str, ...] = tuple(CA_EVENT_COLUMNS)

_STRICT_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_FORBIDDEN_PATH_COMPONENTS = frozenset({"reserve_sealed", "raw", "dsc_evidence"})


@dataclass(frozen=True)
class _Component:
    """The frozen accessor metadata for one D3 component."""

    name: str
    date_column: str
    id_column: str
    schema: tuple[str, ...]
    sort_columns: tuple[str, ...]


_COMPONENTS: dict[str, _Component] = {
    "observations": _Component(
        name="observations",
        date_column="date",
        id_column="ticker",
        schema=OBSERVATION_SCHEMA,
        sort_columns=("ticker", "date", "raw_row_index"),
    ),
    "obs_flags": _Component(
        name="obs_flags",
        date_column="date",
        id_column="ticker",
        schema=FLAG_SCHEMA,
        sort_columns=("ticker", "date", "raw_row_index"),
    ),
    "ca_events": _Component(
        name="ca_events",
        date_column="effective_date",
        id_column="stock_id",
        schema=CA_EVENT_SCHEMA,
        sort_columns=("stock_id", "effective_date", "action_type"),
    ),
}

#: The D3 development file SHA-256 values bound in ``build.json`` (task spec
#: section 1; full values recorded in D3's ``build.json``).
_DEV_FILE_SHA256: dict[str, str] = {
    "observations": (
        "557001d0e9c617befd3c036683f171094e86e19badb760098f40c08f3f56596e"
    ),
    "obs_flags": (
        "256ae77742004ca2ff6d28dbbfc2622bab0b85445cb55cdd667efa5027dea053"
    ),
    "ca_events": (
        "afd4a2986c934d84c2b23eaf68f23ee18746e7c929c6b8625b6c9911e4a2b109"
    ),
}


@dataclass(frozen=True)
class FileBinding:
    """One allowlisted, hash-pinned D3 development file relative to ``<root>``."""

    relative_path: str
    sha256: str


def default_bindings() -> dict[str, FileBinding]:
    """The frozen production accessor bindings (tests inject synthetic ones)."""
    return {
        component: FileBinding(
            f"normalized/{D3_BUILD_ID}/{component}.parquet",
            _DEV_FILE_SHA256[component],
        )
        for component in _COMPONENTS
    }


DEFAULT_BINDINGS: dict[str, FileBinding] = default_bindings()


# ---------------------------------------------------------------------------
# Typed errors (fail closed)
# ---------------------------------------------------------------------------


class AccessorError(Exception):
    """Base class for every D8 development-accessor error."""


class AccessorInputError(AccessorError, ValueError):
    """An accessor argument is malformed (before any file is opened)."""


class ReserveFirewallError(AccessorError):
    """A request would touch the sealed reserve or an unsafe path."""


class D8FirewallError(ReserveFirewallError):
    """A firewall-forbidden column or materialization was requested."""


# ---------------------------------------------------------------------------
# Argument helpers
# ---------------------------------------------------------------------------


def _parse_date(value: Any, label: str) -> date:
    """Accept only a ``datetime.date`` or a strict ``YYYY-MM-DD`` string.

    A ``datetime`` (including a tz-aware or naive ``Timestamp``) and every
    other type are rejected: the accessor never silently truncates a time and
    never infers a session from a timestamp.
    """
    if isinstance(value, datetime):
        raise AccessorInputError(
            f"{label} must be a date or 'YYYY-MM-DD' string, got "
            f"{type(value).__name__}"
        )
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        if _STRICT_DATE_RE.match(value) is None:
            raise AccessorInputError(
                f"{label} must be a strict YYYY-MM-DD string, got {value!r}"
            )
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise AccessorInputError(f"{label} is not a valid date: {exc}") from exc
    raise AccessorInputError(
        f"{label} must be a date or 'YYYY-MM-DD' string, got "
        f"{type(value).__name__}"
    )


def _parse_columns(value: Any, schema: tuple[str, ...]) -> list[str]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise AccessorInputError("columns must be a sequence of column names")
    columns = list(value)
    for column in columns:
        if not isinstance(column, str):
            raise AccessorInputError("columns entries must be strings")
        if column not in schema:
            raise AccessorInputError(
                f"unknown column {column!r} for this component"
            )
    return columns


def _parse_tickers(value: Any) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise AccessorInputError("tickers must be a non-empty list of strings")
    tickers = list(value)
    if not tickers:
        raise AccessorInputError("tickers must be a non-empty list of strings")
    for ticker in tickers:
        if not isinstance(ticker, str) or not ticker:
            raise AccessorInputError("tickers entries must be non-empty strings")
    return tickers


def _sha256_file(path: Path, *, chunk_size: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# Accessor
# ---------------------------------------------------------------------------


class DevelopmentAccessor:
    """The firewall-enforced D3 development accessor (task spec section 2).

    Parameters
    ----------
    root:
        The data-store root (e.g. ``data_store/discovery_dataset_v1``).
    bindings:
        Optional mapping ``component -> FileBinding``.  The default is the
        frozen production binding; tests inject synthetic bindings.
    """

    def __init__(
        self,
        root: str | Path,
        *,
        bindings: Mapping[str, FileBinding] | None = None,
    ) -> None:
        self._root = Path(root)
        if bindings is None:
            self._bindings: dict[str, FileBinding] = dict(DEFAULT_BINDINGS)
        else:
            self._bindings = dict(bindings)

    # -- path safety -------------------------------------------------------

    def _resolve_path(self, component: str) -> Path:
        binding = self._bindings.get(component)
        if binding is None:
            raise ReserveFirewallError(
                f"no frozen binding for component {component!r}"
            )
        relative = binding.relative_path
        parts = PurePosixPath(relative).parts
        if ".." in parts or any(
            part in _FORBIDDEN_PATH_COMPONENTS for part in parts
        ):
            raise ReserveFirewallError(
                f"refusing forbidden path component in {relative!r}"
            )
        base = (self._root / "normalized" / D3_BUILD_ID).resolve()
        resolved = (self._root / relative).resolve()
        if not resolved.is_relative_to(base):
            raise ReserveFirewallError(
                f"refusing path outside the development directory: {relative!r}"
            )
        return resolved

    # -- public API --------------------------------------------------------

    def read(
        self,
        component: str,
        *,
        start: Any,
        end: Any,
        columns: Any,
        tickers: Any = None,
    ) -> pd.DataFrame:
        """Read development rows for ``component`` in ``[start, end]``.

        Every validation runs before any file is opened.  A request whose
        ``end`` is on or after ``2023-07-03`` raises
        :class:`ReserveFirewallError`.
        """
        meta = _COMPONENTS.get(component)
        if meta is None:
            raise AccessorInputError(f"unknown component {component!r}")

        # 1. Parse and validate the arguments.
        start_date = _parse_date(start, "start")
        end_date = _parse_date(end, "end")
        selected = _parse_columns(columns, meta.schema)
        ticker_values = _parse_tickers(tickers)

        # 2. Ordered range.
        if start_date > end_date:
            raise AccessorInputError(
                f"start ({start_date}) is after end ({end_date})"
            )

        # 3. Boundary: anything touching the reserve is refused.
        if end_date >= RESERVE_START:
            raise ReserveFirewallError(
                f"request end {end_date} touches the reserve boundary "
                f"{RESERVE_START}"
            )

        # 4. Resolve the path only from the bindings.
        path = self._resolve_path(component)

        # 5. Verify the bound SHA-256 before reading.
        actual = _sha256_file(path)
        if actual != self._bindings[component].sha256:
            raise ReserveFirewallError(
                f"development file {component!r} does not match its binding"
            )

        # 6. Read with the frozen projection and pyarrow filters only.
        read_columns = list(
            dict.fromkeys(
                [*selected, meta.date_column, *meta.sort_columns]
            )
        )
        filters: list[tuple[str, str, Any]] = [
            (meta.date_column, ">=", start_date),
            (meta.date_column, "<=", end_date),
        ]
        if ticker_values is not None:
            filters.append((meta.id_column, "in", list(ticker_values)))
        table = pq.read_table(path, columns=read_columns, filters=filters)
        frame = table.to_pandas()

        # 7. Post-load assertion (defense in depth).
        if not frame.empty:
            observed = pd.to_datetime(frame[meta.date_column]).dt.date
            out_of_range = (observed < start_date) | (observed > end_date)
            in_reserve = observed >= RESERVE_START
            if bool((out_of_range | in_reserve).any()):
                raise ReserveFirewallError(
                    "a returned row lies in the reserve or outside the "
                    "requested range"
                )

        # 8. Deterministic, stable ordering.
        if frame.empty:
            result = frame
        else:
            result = frame.sort_values(
                list(meta.sort_columns), kind="stable"
            ).reset_index(drop=True)
        return result[selected]
