"""Campaign-3 P7 / R7-T — fold-generic re-expression of the frozen D7c chain.

The frozen D7c builder (``smart_beta.dataset_v1.trusted_inputs``) is the sole
authority for the development ``TrustedInput`` artifact ``ti-aa23fabd3db4e595``
(``ti_hash 30ee0c51…``).  It hard-codes the development boundary and refuses
every reserve date by design (the firewall).  Amendment A1
(``docs/campaign3_p7_amendment_a1.md``) freezes the reserve path as **new,
fold-generic code** that re-expresses the same logic with an explicit
:class:`FoldWindow`, leaving the pinned development builder untouched.

This module implements the D7c slice: the three contract-§7 inputs
``daily_total_return``, ``total_return_index`` and ``dollar_volume`` plus the
TRI ``segments`` table, on a ``FoldWindow``.  It adds:

* **Dev/reserve stitching.**  For a reserve fold every *source* row dated
  ``< 2023-07-03`` comes from the development packages and every row dated
  ``>= 2023-07-03`` from the reserve packages
  (:class:`StitchedAccessor`, :func:`_load_stitched_day_flags`).
* **The one-session bridge lag.**  Row ``d`` carries the primitive of
  ``prev(d)``; the first reserve grid row therefore carries a development
  primitive, which is the bridge across the boundary.
* **The fold window.**  The grid, the lookback floor (up to 504 sessions), the
  corporate-action ``as_of`` and the D-SC class/policy query boundary are all
  taken from the fold instead of the development constants.

Equivalence is proven in ``tests/test_reserve_chain_trusted.py``: on synthetic
development inputs the module reproduces ``build_trusted_inputs``'s tables and
per-file bytes for the same synthetic store.

Nothing here reads a real ``data_store``, a provider call or a credential;
tests inject synthetic packages.  This module never reads a ``reserve_sealed``
path itself; the reserve accessor is supplied by the caller (R7-X from an
``AccessGrant``).

Reconciliation: :class:`FoldWindow`, :class:`AccessGrant` and
:class:`ReserveAccessDenied` are imported from the R7-U-owned
``reserve_chain/fold.py`` (reconciled at merge commit ``3fc6531``).  A reserve
fold cannot be constructed at all without an ``AccessGrant``, and
:func:`build_trusted_fold` re-checks the grant first (defense in depth) so a
forged grantless reserve fold is refused with :class:`ReserveAccessDenied`
*before any path is opened*.

D7c grid vs D6 schedule: the frozen D7c grid starts at ``CALENDAR_START``
(2015-01-02), one schedule year before the D6 formation schedule start; the
development fold used here therefore spans ``CALENDAR_START..DEVELOPMENT_END``
(:func:`development_fold`), so the full development equivalence with the frozen
``ti-aa23fabd3db4e595`` holds.
"""

from __future__ import annotations

import argparse
import bisect
import math
import shutil
import tempfile
from dataclasses import dataclass, replace
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from smart_beta.config.settings import DEFAULT_SETTINGS
from smart_beta.dataset_v1 import trusted_inputs as T
from smart_beta.dataset_v1.classification import (
    class_state_at,
    load_class_timeline,
    load_policy_timeline,
    policy_state_at,
)
from smart_beta.dataset_v1.reserve_chain.fold import (
    DEVELOPMENT,
    RESERVE,
    AccessGrant,
    FoldWindow,
    ReserveAccessDenied,
)
from smart_beta.dataset_v1.reserve_firewall import (
    DevelopmentAccessor,
    ReserveFirewallError,
)
from smart_beta.pit.corporate_actions import compute_adjusted_returns
from smart_beta.vendors.tiingo.calendar_source import build_nyse_calendar

__all__ = [
    "SOURCE_DEVELOPMENT",
    "SOURCE_RESERVE",
    "DEVELOPMENT",
    "RESERVE",
    "RESERVE_START",
    "CALENDAR_START",
    "DEVELOPMENT_END",
    "LOOKBACK_SESSIONS",
    "FoldWindow",
    "AccessGrant",
    "ReserveAccessDenied",
    "development_fold",
    "TrustedFoldError",
    "TrustedFoldInputError",
    "TrustedFoldConsistencyError",
    "StitchedAccessor",
    "build_trusted_fold",
    "read_fold_frames",
    "main",
]


# ---------------------------------------------------------------------------
# Frozen constants (imported read-only from the pinned D7c module)
# ---------------------------------------------------------------------------

#: Source aliases (R7-U's ``fold.py`` owns the canonical values).
SOURCE_DEVELOPMENT = DEVELOPMENT
SOURCE_RESERVE = RESERVE

#: The sealed reserve boundary (contract §§11.2, 12).
RESERVE_START = T.RESERVE_START
#: The development grid end.
DEVELOPMENT_END = T.DEVELOPMENT_END
#: Trusted-calendar bounds.
CALENDAR_START = T.CALENDAR_START
CALENDAR_END = T.CALENDAR_END

#: The maximum factor lookback (P-Q v2.4 ED ``lookback_max`` = 504 sessions).
LOOKBACK_SESSIONS = 504

#: The default output directory under ``<root>``.
DEFAULT_OUTPUT_PREFIX = "derived/trusted_inputs"


# ---------------------------------------------------------------------------
# Typed errors (fail closed)
# ---------------------------------------------------------------------------


class TrustedFoldError(Exception):
    """Base class for every reserve-chain trusted-input error."""


class TrustedFoldInputError(TrustedFoldError, ValueError):
    """A required fold input is missing, unreadable or malformed."""


class TrustedFoldConsistencyError(TrustedFoldError):
    """A frozen D7c consistency invariant is violated on a fold."""


# ---------------------------------------------------------------------------
# Fold window
# ---------------------------------------------------------------------------


def development_fold() -> FoldWindow:
    """The D7c development fold: ``CALENDAR_START..DEVELOPMENT_END``.

    This is deliberately *not* ``FoldWindow.development()``: D6 formations
    start at the 2016 schedule start, while the frozen D7c grid starts at the
    2015 calendar start, and the frozen development equivalence covers the
    full grid.
    """
    return FoldWindow(CALENDAR_START, DEVELOPMENT_END, DEVELOPMENT)


def _fold_payload(fold: FoldWindow) -> dict[str, str]:
    """A JSON-safe summary of the fold recorded in the artifact manifest."""
    return {
        "start": fold.start.isoformat(),
        "end": fold.end.isoformat(),
        "source": fold.source,
        "partition": fold.partition,
    }


# ---------------------------------------------------------------------------
# Fold session geometry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _FoldGrid:
    sessions: list[date]
    pos: Mapping[date, int]
    grid_sessions: list[date]
    interval_floor: date
    source_end: date


def _fold_grid(
    calendar: Any,
    fold: FoldWindow,
    *,
    lookback_sessions: int = LOOKBACK_SESSIONS,
) -> _FoldGrid:
    sessions = [ts.date() for ts in calendar.dates]
    pos = {d: i for i, d in enumerate(sessions)}
    start_idx = bisect.bisect_left(sessions, fold.start)
    end_idx = bisect.bisect_right(sessions, fold.end) - 1
    if end_idx < start_idx:
        raise TrustedFoldInputError(
            f"fold {fold.start}..{fold.end} contains no trusted session"
        )
    grid_sessions = sessions[start_idx : end_idx + 1]
    # The last primitive a new grid row can carry is prev(last_row).
    source_end = sessions[end_idx - 1] if end_idx > 0 else grid_sessions[0]
    if fold.source == RESERVE:
        floor_idx = max(0, start_idx - lookback_sessions)
    else:
        floor_idx = 0
    return _FoldGrid(
        sessions=sessions,
        pos=pos,
        grid_sessions=grid_sessions,
        interval_floor=sessions[floor_idx],
        source_end=source_end,
    )


# ---------------------------------------------------------------------------
# Stitched accessor (dev rows before the boundary, reserve rows after)
# ---------------------------------------------------------------------------


class StitchedAccessor:
    """A D3 accessor that stitches the development and reserve packages.

    Rows dated ``< boundary`` are read from the development accessor; rows
    dated ``>= boundary`` from the reserve accessor.  A request that spans the
    boundary is split, never read-then-discarded.  This implements the design's
    dev/reserve stitching; the reserve accessor is supplied by R7-X from an
    authorized ``AccessGrant`` (tests inject a synthetic one).
    """

    def __init__(
        self,
        development: Any,
        reserve: Any,
        *,
        boundary: date = RESERVE_START,
    ) -> None:
        self._development = development
        self._reserve = reserve
        self._boundary = boundary

    @property
    def boundary(self) -> date:
        return self._boundary

    def read(
        self,
        component: str,
        *,
        start: date,
        end: date,
        columns: Sequence[str],
        tickers: Sequence[str] | None = None,
    ) -> pd.DataFrame:
        if end < self._boundary:
            return self._development.read(
                component,
                start=start,
                end=end,
                columns=columns,
                tickers=tickers,
            )
        if start >= self._boundary:
            return self._reserve.read(
                component,
                start=start,
                end=end,
                columns=columns,
                tickers=tickers,
            )
        dev_end = self._boundary - timedelta(days=1)
        left = self._development.read(
            component,
            start=start,
            end=dev_end,
            columns=columns,
            tickers=tickers,
        )
        right = self._reserve.read(
            component,
            start=self._boundary,
            end=end,
            columns=columns,
            tickers=tickers,
        )
        return pd.concat([left, right], ignore_index=True)


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

_OBSERVATION_COLUMNS = ("ticker", "date", "close", "volume", "raw_row_index")
_CA_EVENT_COLUMNS = (
    "stock_id",
    "effective_date",
    "action_type",
    "knowledge_date",
    "adjustment_factor",
    "is_superseded",
)
_IDENTITY_COLUMNS = (
    "security_key",
    "ticker",
    "listed_start",
    "listed_end",
    "state",
)
_DAY_FLAG_COLUMNS = (
    "ticker",
    "date",
    "raw_row_index",
    "class_or_flag",
    "disposition",
)


def _load_stitched_day_flags(
    root: Path,
    development_bindings: T.Bindings,
    reserve_bindings: T.Bindings,
) -> pd.DataFrame:
    """D5 day flags stitched at the reserve boundary."""
    dev = T.Resolver(root, development_bindings).read_parquet(
        development_bindings.d5.files[0].relative_path,
        columns=list(_DAY_FLAG_COLUMNS),
    )
    reserve = T.Resolver(root, reserve_bindings).read_parquet(
        reserve_bindings.d5.files[0].relative_path,
        columns=list(_DAY_FLAG_COLUMNS),
    )
    dev = dev[
        pd.to_datetime(dev["date"]).dt.date < RESERVE_START
    ].reset_index(drop=True)
    reserve = reserve[
        pd.to_datetime(reserve["date"]).dt.date >= RESERVE_START
    ].reset_index(drop=True)
    return pd.concat([dev, reserve], ignore_index=True)


def _load_fold_inputs(
    root: Path,
    fold: FoldWindow,
    bindings: T.Bindings,
    accessor: Any,
    grid: _FoldGrid,
    reserve_bindings: T.Bindings | None,
) -> T._Inputs:
    observations = accessor.read(
        "observations",
        start=CALENDAR_START,
        end=grid.source_end,
        columns=list(_OBSERVATION_COLUMNS),
    )
    ca_events = accessor.read(
        "ca_events",
        start=CALENDAR_START,
        end=grid.source_end,
        columns=list(_CA_EVENT_COLUMNS),
    )
    if fold.source == RESERVE:
        if reserve_bindings is None:
            raise TrustedFoldInputError(
                "a reserve fold requires reserve_bindings"
            )
        reserve_resolver = T.Resolver(root, reserve_bindings)
        identity = reserve_resolver.read_parquet(
            reserve_bindings.d4.files[0].relative_path,
            columns=list(_IDENTITY_COLUMNS),
        )
        day_flags = _load_stitched_day_flags(
            root, bindings, reserve_bindings
        )
        class_timeline = load_class_timeline(
            reserve_resolver.resolve(
                reserve_bindings.dsc.files[0].relative_path
            )
        )
        policy_timeline = load_policy_timeline(
            reserve_resolver.resolve(
                reserve_bindings.dsc.files[1].relative_path
            )
        )
    else:
        resolver = T.Resolver(root, bindings)
        identity = resolver.read_parquet(
            bindings.d4.files[0].relative_path,
            columns=list(_IDENTITY_COLUMNS),
        )
        day_flags = resolver.read_parquet(
            bindings.d5.files[0].relative_path,
            columns=list(_DAY_FLAG_COLUMNS),
        )
        class_timeline = load_class_timeline(
            resolver.resolve(bindings.dsc.files[0].relative_path)
        )
        policy_timeline = load_policy_timeline(
            resolver.resolve(bindings.dsc.files[1].relative_path)
        )
    return T._Inputs(
        observations=observations,
        ca_events=ca_events,
        identity=identity,
        day_flags=day_flags,
        class_timeline=class_timeline,
        policy_timeline=policy_timeline,
    )


def _post_load_assertions(
    inputs: T._Inputs, fold: FoldWindow, source_end: date
) -> None:
    """Fail closed on any source row outside the fold's declared window.

    The development fold additionally mirrors D7c's reserve firewall: every
    loaded source date must be strictly before ``RESERVE_START``.
    """

    def _parsed(series: pd.Series) -> pd.Series:
        return pd.to_datetime(series, errors="coerce")

    checks = (
        (inputs.observations["date"], "D3 observations"),
        (inputs.day_flags["date"], "D5 day flags"),
        (inputs.ca_events["effective_date"], "D3 corporate actions"),
        (inputs.ca_events["knowledge_date"], "D3 corporate actions"),
    )
    for series, what in checks:
        parsed = _parsed(series)
        if fold.source != RESERVE and bool(
            (parsed >= pd.Timestamp(RESERVE_START)).any()
        ):
            raise ReserveFirewallError(
                f"{what} contains a reserve date on a development fold"
            )
        if bool((parsed > pd.Timestamp(source_end)).any()):
            raise TrustedFoldInputError(
                f"{what} contains a date after the fold source end "
                f"{source_end}"
            )


# ---------------------------------------------------------------------------
# Corporate actions (fold ``as_of``)
# ---------------------------------------------------------------------------


def _adjusted_returns(
    key: T._Key,
    valid_returns: Mapping[date, float],
    ca_events: Sequence[T._CaEvent],
    *,
    as_of: date,
) -> dict[date, float]:
    """D7c's ``_adjusted_returns`` with an explicit fold ``as_of``."""
    if not valid_returns:
        return {}
    ordered = sorted(valid_returns)
    raw_panel = pd.DataFrame(
        {
            "date": pd.to_datetime(ordered),
            "stock_id": pd.Series(
                [key.security_key] * len(ordered), dtype="string"
            ),
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
                    [event.action_type for event in ca_events],
                    dtype="string",
                ),
                "knowledge_date": pd.to_datetime(
                    [event.knowledge_date for event in ca_events]
                ),
                "adjustment_factor": np.array(
                    [event.adjustment_factor for event in ca_events],
                    dtype=np.float64,
                ),
                "is_superseded": pd.Series(
                    [False] * len(ca_events), dtype=bool
                ),
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
    settings = replace(
        DEFAULT_SETTINGS,
        pit_availability_buffer_days=T.PIT_BUFFER_DAYS,
    )
    adjusted = compute_adjusted_returns(
        raw_panel, ca_panel, as_of=as_of, calendar=None, settings=settings
    )
    result: dict[date, float] = {}
    for record in adjusted.to_dict("records"):
        d = T._as_date(record["date"])
        value = T._as_float(record["adj_ret"])
        if d is not None and value is not None:
            result[d] = value
    return result


def _compute_key_primitives(
    key: T._Key,
    interval: Sequence[date],
    sessions: Sequence[date],
    pos: Mapping[date, int],
    rows_by_ticker: Mapping[str, Mapping[date, list[T._Row]]],
    unusable: set[tuple[str, date, int]],
    ca_events: Sequence[T._CaEvent],
    *,
    as_of: date,
) -> T._KeyPrimitives:
    """D7c's ``_compute_key_primitives`` with a fold ``as_of``."""
    ticker_rows = rows_by_ticker.get(key.ticker, {})
    usable: dict[date, T._Row] = {}
    for s in interval:
        session_rows = ticker_rows.get(s, [])
        candidates = T._usable_rows(
            session_rows, key.ticker, s, unusable
        )
        if len(candidates) > 1:
            raise TrustedFoldConsistencyError(
                f"{key.security_key!r} has {len(candidates)} usable rows on "
                f"session {s}"
            )
        if candidates:
            usable[s] = candidates[0]

    raw_returns: dict[date, float] = {}
    for s in interval:
        p = T._primitive_session(s, sessions, pos)
        if p is None:
            continue
        if s in usable and p in usable:
            raw_returns[s] = usable[s].close / usable[p].close - 1.0

    returns = _adjusted_returns(key, raw_returns, ca_events, as_of=as_of)
    for s, value in returns.items():
        if not math.isfinite(value) or value <= -1.0:
            raise TrustedFoldConsistencyError(
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

    segments, tri = T._compute_segments(interval, usable, returns)
    return T._KeyPrimitives(
        returns=returns,
        dollar_volume=dollar_volume,
        tri=tri,
        segments=segments,
        valid_returns=set(returns),
    )


# ---------------------------------------------------------------------------
# Fold-aware D-SC guards
# ---------------------------------------------------------------------------


def _guarded_class_state_at(
    timeline: Mapping[str, Any],
    key: str,
    cutoff: date,
    boundary: date,
) -> tuple[str, str]:
    if cutoff > boundary:
        raise ReserveFirewallError(
            f"D-SC class query cutoff {cutoff} is after {boundary}"
        )
    return class_state_at(timeline, key, cutoff)


def _guarded_policy_state_at(
    timeline: Mapping[str, Any],
    key: str,
    cutoff: date,
    boundary: date,
) -> tuple[str, frozenset[str]]:
    if cutoff > boundary:
        raise ReserveFirewallError(
            f"D-SC policy query cutoff {cutoff} is after {boundary}"
        )
    return policy_state_at(timeline, key, cutoff)


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def _compute_fold_ti_id(
    bindings: T.Bindings, code_sha: str, fold: FoldWindow
) -> str:
    payload = {
        "bindings": T._bindings_payload(bindings),
        "trusted_fold_sha256": code_sha,
        "fold": _fold_payload(fold),
    }
    return "ti-" + T._sha256_hex(
        T.canonical_json(payload).encode("utf-8")
    )[:16]


def _write_outputs(
    root: Path,
    ti_id: str,
    frames: Mapping[str, pd.DataFrame],
    segments: pd.DataFrame,
    content: dict[str, Any],
    *,
    output_prefix: str,
) -> dict[str, Any]:
    """The frozen D7c writer, parameterized by the output prefix."""
    derived = root / output_prefix
    derived.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=ti_id + ".", dir=derived))
    try:
        produced: dict[str, Path] = {}
        for name in T.INPUT_NAMES:
            path = stage / T._INPUT_FILES[name]
            frames[name].to_parquet(
                path, engine="pyarrow", index=False, compression="zstd"
            )
            produced[
                f"{output_prefix}/{ti_id}/{T._INPUT_FILES[name]}"
            ] = path
        segments_path = stage / T.SEGMENTS_FILE
        segments.to_parquet(
            segments_path, engine="pyarrow", index=False, compression="zstd"
        )
        produced[f"{output_prefix}/{ti_id}/{T.SEGMENTS_FILE}"] = segments_path

        files_sha256 = {
            relative: T._sha256_file(path)
            for relative, path in produced.items()
        }
        content["files_sha256"] = files_sha256
        content["ti_hash"] = T._sha256_hex(
            T.canonical_json(files_sha256).encode("utf-8")
        )
        (stage / T.MANIFEST_FILE).write_bytes(T._canonical_bytes(content))

        target = derived / ti_id
        if target.exists():
            existing = sorted(p.name for p in target.iterdir())
            staged = sorted(p.name for p in stage.iterdir())
            if existing != staged or any(
                (target / name).read_bytes()
                != (stage / name).read_bytes()
                for name in staged
            ):
                raise TrustedFoldConsistencyError(
                    f"existing {target} holds different bytes for {ti_id}"
                )
            shutil.rmtree(stage)
        else:
            stage.rename(target)
        return content
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def read_fold_frames(
    root: Path | str,
    content: Mapping[str, Any],
    *,
    output_prefix: str = DEFAULT_OUTPUT_PREFIX,
) -> dict[str, pd.DataFrame]:
    """Read the four output tables of a fold artifact (tests / R7-X)."""
    directory = (
        Path(root) / output_prefix / str(content["ti_id"])
    )
    frames = {
        name: pd.read_parquet(directory / T._INPUT_FILES[name])
        for name in T.INPUT_NAMES
    }
    frames["segments"] = pd.read_parquet(directory / T.SEGMENTS_FILE)
    return frames


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def build_trusted_fold(
    root: Path | str,
    *,
    fold: FoldWindow,
    bindings: T.Bindings | None = None,
    calendar: Any | None = None,
    accessor: Any | None = None,
    reserve_bindings: T.Bindings | None = None,
    reserve_accessor: Any | None = None,
    output_prefix: str = DEFAULT_OUTPUT_PREFIX,
    lookback_sessions: int = LOOKBACK_SESSIONS,
) -> dict[str, Any]:
    """Build the D7c trusted inputs for ``fold`` and return the manifest.

    The development fold reproduces the pinned ``build_trusted_inputs``.  The
    reserve fold stitches the development packages (rows before
    ``RESERVE_START``) with the reserve packages via ``reserve_bindings`` and
    ``reserve_accessor`` (supplied by R7-X from an ``AccessGrant``); the
    reserve accessor is the only object that may open a reserve package.
    """
    if not isinstance(fold, FoldWindow):
        raise TrustedFoldInputError("fold must be a FoldWindow")
    if fold.source == RESERVE and not isinstance(fold.grant, AccessGrant):
        # Defense in depth: R7-U's FoldWindow already refuses this at
        # construction.  Re-check here so a forged grantless reserve fold is
        # denied before any path is opened.
        raise ReserveAccessDenied(
            "a reserve trusted-input derivation requires an AccessGrant; "
            "refusing before any path is opened"
        )
    bindings = bindings if bindings is not None else T.DEFAULT_BINDINGS
    if not isinstance(bindings, T.Bindings):
        raise TrustedFoldInputError("bindings must be a Bindings value")
    if fold.source == RESERVE and reserve_bindings is not None:
        if not isinstance(reserve_bindings, T.Bindings):
            raise TrustedFoldInputError(
                "reserve_bindings must be a Bindings value"
            )

    calendar = (
        calendar
        if calendar is not None
        else build_nyse_calendar(CALENDAR_START, CALENDAR_END)
    )
    root = Path(root)
    grid = _fold_grid(
        calendar, fold, lookback_sessions=lookback_sessions
    )

    documents = T._verify_documents(bindings)
    T._verify_pinned_code(bindings)
    code_sha = T._sha256_file(Path(__file__))
    ti_id = _compute_fold_ti_id(bindings, code_sha, fold)

    for stage in bindings.stages():
        T._verify_stage(T.Resolver(root, bindings), stage)
    if fold.source == RESERVE:
        if reserve_bindings is None:
            raise TrustedFoldInputError(
                "a reserve fold requires reserve_bindings"
            )
        for stage in reserve_bindings.stages():
            T._verify_stage(T.Resolver(root, reserve_bindings), stage)

    if accessor is None:
        accessor = DevelopmentAccessor(
            root, bindings=T._accessor_bindings(bindings)
        )
    if fold.source == RESERVE:
        if reserve_accessor is None:
            raise TrustedFoldInputError(
                "a reserve fold requires a reserve_accessor"
            )
        accessor = StitchedAccessor(accessor, reserve_accessor)

    inputs = _load_fold_inputs(
        root, fold, bindings, accessor, grid, reserve_bindings
    )
    _post_load_assertions(inputs, fold, grid.source_end)

    sessions = grid.sessions
    pos = grid.pos
    grid_sessions = grid.grid_sessions
    interval_floor = grid.interval_floor

    keys = T._identity_keys(inputs.identity)
    rows_by_ticker, unusable = T._index_rows(
        inputs.observations, inputs.day_flags
    )
    ca_by_ticker = T._index_ca_events(inputs.ca_events)
    session_set = frozenset(sessions)

    daily_records: list[tuple[date, str, date, float]] = []
    tri_records: list[tuple[date, str, date, float]] = []
    dv_records: list[tuple[date, str, date, float]] = []
    segment_records: list[tuple[str, int, date, date, bool]] = []
    dsc_max_query_cutoff: date | None = None
    mask_counts: dict[str, dict[str, int]] = {
        name: {
            "admitted": 0,
            "class_masked": 0,
            "policy_masked": 0,
            "invalid": 0,
            "segment_masked": 0,
        }
        for name in T.INPUT_NAMES
    }

    for key in keys:
        upper = min(key.listed_end, fold.end)
        interval = [
            s for s in sessions if key.listed_start <= s <= upper
        ]
        if fold.source == RESERVE:
            interval = [s for s in interval if s >= interval_floor]
        if not interval:
            continue
        events = T._member_ca_events(
            ca_by_ticker.get(key.ticker, []),
            key.listed_start,
            min(upper, grid.source_end),
        )
        T._validate_ca_sessions(events, session_set)
        primitives = _compute_key_primitives(
            key,
            interval,
            sessions,
            pos,
            rows_by_ticker,
            unusable,
            events,
            as_of=fold.end,
        )
        for segment_id, anchor, last, admitted in primitives.segments:
            segment_records.append(
                (key.security_key, segment_id, anchor, last, admitted)
            )

        for row_date in grid_sessions:
            source_session = T._primitive_session(row_date, sessions, pos)
            if source_session is None:
                continue
            if (
                dsc_max_query_cutoff is None
                or source_session > dsc_max_query_cutoff
            ):
                dsc_max_query_cutoff = source_session
            class_state, _ = _guarded_class_state_at(
                inputs.class_timeline,
                key.security_key,
                source_session,
                grid.source_end,
            )
            if class_state != T.CLASS_COMMON_STOCK:
                mask_counts[T.INPUT_DAILY_TOTAL_RETURN][
                    "class_masked"
                ] += 1
                mask_counts[T.INPUT_TOTAL_RETURN_INDEX][
                    "class_masked"
                ] += 1
                mask_counts[T.INPUT_DOLLAR_VOLUME]["class_masked"] += 1
                continue
            policy_state, _ = _guarded_policy_state_at(
                inputs.policy_timeline,
                key.security_key,
                source_session,
                grid.source_end,
            )
            if policy_state != T.POLICY_NO_EXCLUSION:
                mask_counts[T.INPUT_DAILY_TOTAL_RETURN][
                    "policy_masked"
                ] += 1
                mask_counts[T.INPUT_TOTAL_RETURN_INDEX][
                    "policy_masked"
                ] += 1
                mask_counts[T.INPUT_DOLLAR_VOLUME]["policy_masked"] += 1
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
                mask_counts[T.INPUT_DAILY_TOTAL_RETURN]["admitted"] += 1
            else:
                mask_counts[T.INPUT_DAILY_TOTAL_RETURN]["invalid"] += 1

            if source_session in primitives.dollar_volume:
                dv_records.append(
                    (
                        row_date,
                        key.security_key,
                        source_session,
                        primitives.dollar_volume[source_session],
                    )
                )
                mask_counts[T.INPUT_DOLLAR_VOLUME]["admitted"] += 1
            else:
                mask_counts[T.INPUT_DOLLAR_VOLUME]["invalid"] += 1

            if source_session in primitives.tri:
                tri_records.append(
                    (
                        row_date,
                        key.security_key,
                        source_session,
                        primitives.tri[source_session],
                    )
                )
                mask_counts[T.INPUT_TOTAL_RETURN_INDEX]["admitted"] += 1
            elif source_session in primitives.valid_returns:
                mask_counts[T.INPUT_TOTAL_RETURN_INDEX][
                    "segment_masked"
                ] += 1
            else:
                mask_counts[T.INPUT_TOTAL_RETURN_INDEX]["invalid"] += 1

    frames = {
        T.INPUT_DAILY_TOTAL_RETURN: T._build_long(daily_records),
        T.INPUT_TOTAL_RETURN_INDEX: T._build_long(tri_records),
        T.INPUT_DOLLAR_VOLUME: T._build_long(dv_records),
    }
    for name, frame in frames.items():
        _assert_output_dates(frame, name, fold)
    segments = T._build_segments(segment_records)

    columns = [key.security_key for key in keys]
    first_row = grid_sessions[0] if grid_sessions else None
    last_row = grid_sessions[-1] if grid_sessions else None
    capabilities = T._capabilities(len(grid_sessions))

    content: dict[str, Any] = {
        "schema": T.SCHEMA,
        "ti_id": ti_id,
        "ti_code_sha256": code_sha,
        "fold": _fold_payload(fold),
        "fold_source_end": grid.source_end.isoformat(),
        "bindings": T._bindings_payload(bindings),
        "documents": dict(documents),
        "pinned_code": {
            code.name: code.sha256 for code in bindings.pinned_code
        },
        "grid": {
            "first_row": first_row.isoformat() if first_row else None,
            "last_row": last_row.isoformat() if last_row else None,
            "row_count": len(grid_sessions),
            "columns": columns,
            "columns_sha256": T._sha256_hex(
                T.canonical_json(columns).encode("utf-8")
            ),
        },
        "capabilities": {
            name: cap.to_dict() for name, cap in capabilities.items()
        },
        "knowledge_date_rule": T.KNOWLEDGE_DATE_RULE,
        "dsc_max_query_cutoff": (
            dsc_max_query_cutoff.isoformat()
            if dsc_max_query_cutoff is not None
            else None
        ),
        "mask_counts": mask_counts,
        "certification_boundary": T.CERTIFICATION_BOUNDARY,
    }
    return _write_outputs(
        root, ti_id, frames, segments, content, output_prefix=output_prefix
    )


def _assert_output_dates(
    frame: pd.DataFrame, name: str, fold: FoldWindow
) -> None:
    if len(frame) == 0:
        return
    row_dates = pd.to_datetime(frame["row_date"], errors="coerce")
    source_dates = pd.to_datetime(frame["source_session"], errors="coerce")
    if bool(
        (row_dates < pd.Timestamp(fold.start)).any()
        or (row_dates > pd.Timestamp(fold.end)).any()
    ):
        raise TrustedFoldConsistencyError(
            f"{name} output has a row_date outside {fold.start}..{fold.end}"
        )
    if bool((source_dates > pd.Timestamp(fold.end)).any()):
        raise TrustedFoldConsistencyError(
            f"{name} output has a source_session after {fold.end}"
        )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build the fold-generic D7c trusted inputs."
    )
    parser.add_argument("--root", required=True, help="data-store root")
    parser.add_argument(
        "--source",
        choices=(SOURCE_DEVELOPMENT, SOURCE_RESERVE),
        default=SOURCE_DEVELOPMENT,
    )
    parser.add_argument("--start", default=None, help="fold start YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="fold end YYYY-MM-DD")
    args = parser.parse_args(argv)
    if args.source == SOURCE_RESERVE:
        raise SystemExit(
            "a reserve fold requires an AccessGrant (R7-X) and a reserve "
            "accessor; it is not runnable from this CLI"
        )
    start = (
        date.fromisoformat(args.start) if args.start else CALENDAR_START
    )
    fold = FoldWindow(start, date.fromisoformat(args.end), DEVELOPMENT)
    content = build_trusted_fold(args.root, fold=fold)
    print(content["ti_id"])
    print(content["ti_hash"])
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
