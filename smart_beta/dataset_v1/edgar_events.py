"""E-3 — Dataset v1.3 SEC EDGAR event normalization, timing and firewall split.

Authority:

* the frozen design ``docs/discovery_dataset_v1_3_edgar_events_design.md``
  (``c9daa54b…b269``), "V§n" below;
* the frozen plan ``docs/discovery_dataset_v1_3_implementation_plan.md`` —
  §1 row E-3, §2 security, §3 interfaces, §4 timing / event-type / firewall
  tests (owner E-3), §5 mutations (prefix ``E3``), §7 stops.

This module turns E-1's stored ``submissions`` bytes into the V§4 event
panel.  It implements, exactly:

* the V§4 event types — ``EA_8K202`` (an original 8-K whose ``items``
  include 2.02), ``EA_PERIODIC_FALLBACK`` (a 10-Q/10-K with no ``EA_8K202``
  in ``[report period end, filing]``), and the FPI types ``FPI_6K`` /
  ``FPI_20F`` — with amendments never creating an event (V§4);
* the V§4 knowledge-time rule: ``acceptanceDateTime`` (US Eastern time) maps
  to the containing session when it is before 16:00 ET on a trading session,
  otherwise to the next trading session; ``filingDate`` is never used for
  K(e); an ``8-K/A`` never creates or moves an event (V§4);
* the M1b §3 boundary fact ``B_e`` — acceptance within ±15 minutes of the
  session close (16:00 ET by default, an injected early-close time otherwise)
  — recorded as a fact, never applied to K(e);
* the V§5 firewall split: every row with ``k_session`` at or after
  ``2023-07-03`` goes to ``reserve_sealed/edgar-<acq_id>/`` only, and the
  development loader fails closed (V§5, plan §3).

The module is offline: it reads only the byte-exact E-1 raw layout provided
by the caller (never ``data_store``), imports existing modules read-only,
and makes no provider or network call.  The trusted D3/D7c session calendar
is accepted as an argument (``smart_beta.vendors.tiingo.calendar_source``
is imported only to build the default calendar, exactly as the other
Dataset-v1 modules do).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from smart_beta.dataset_v1.edgar_fetch import (
    iter_filings,
    validate_submission_payload,
)
from smart_beta.dataset_v1.reserve_firewall import RESERVE_START
from smart_beta.pit.calendar import TradingCalendar
from smart_beta.vendors.tiingo.calendar_source import build_nyse_calendar

__all__ = [
    # constants
    "BOUNDARY_WINDOW_MINUTES",
    "EASTERN",
    "EA_8K202",
    "EA_PERIODIC_FALLBACK",
    "EVENT_COLUMNS",
    "EVENT_SCHEMA",
    "EVENT_TYPES",
    "FPI_6K",
    "FPI_20F",
    "MARKET_CLOSE_ET",
    "RESERVE_START",
    # errors
    "EventError",
    "EventFirewallError",
    "EventFormatError",
    "EventInputError",
    # types
    "EventPanel",
    "EventRow",
    "KeyCik",
    # timing
    "boundary_flag",
    "knowledge_session",
    "parse_acceptance_datetime",
    "to_eastern",
    # normalization
    "classify_form",
    "default_session_calendar",
    "is_amendment",
    "load_raw_filings",
    "load_raw_submissions",
    "normalize_events",
    "normalize_key",
    "parse_items",
    # output / firewall
    "EventDevelopmentAccessor",
    "events_frame",
    "sort_event_rows",
    "split_by_firewall",
    "write_event_panel",
    "write_events_parquet",
]


# ---------------------------------------------------------------------------
# Frozen constants
# ---------------------------------------------------------------------------

#: The V§4 event types.
EA_8K202 = "EA_8K202"
EA_PERIODIC_FALLBACK = "EA_PERIODIC_FALLBACK"
FPI_6K = "FPI_6K"
FPI_20F = "FPI_20F"
EVENT_TYPES: tuple[str, ...] = (EA_8K202, EA_PERIODIC_FALLBACK, FPI_6K, FPI_20F)

#: The frozen 16:00 ET close and the M1b §3 ±15-minute boundary window.
MARKET_CLOSE_ET = time(16, 0)
BOUNDARY_WINDOW_MINUTES = 15

#: The 8-K Item 2.02 token (V§4) and the periodic forms that can fall back.
EA_8K_ITEM = "2.02"
PERIODIC_FALLBACK_FORMS = frozenset({"10-Q", "10-K"})
FPI_6K_FORMS = frozenset({"6-K"})
FPI_ANNUAL_FORMS = frozenset({"20-F", "40-F"})

#: The event panel column order (plan §3).
EVENT_COLUMNS: tuple[str, ...] = (
    "security_key",
    "cik",
    "accession",
    "form",
    "event_type",
    "acceptance_utc",
    "acceptance_et",
    "k_session",
    "boundary_flag",
    "report_period",
)

#: The event panel PyArrow schema (plan §3; deterministic and explicit).
EVENT_SCHEMA = pa.schema(
    [
        pa.field("security_key", pa.string()),
        pa.field("cik", pa.int64()),
        pa.field("accession", pa.string()),
        pa.field("form", pa.string()),
        pa.field("event_type", pa.string()),
        pa.field("acceptance_utc", pa.string()),
        pa.field("acceptance_et", pa.string()),
        pa.field("k_session", pa.date32()),
        pa.field("boundary_flag", pa.bool_()),
        pa.field("report_period", pa.date32()),
    ]
)

EASTERN = ZoneInfo("America/New_York")
_UTC = timezone.utc
_ITEMS_SPLIT_RE = re.compile(r"[,;]")


# ---------------------------------------------------------------------------
# Typed errors (fail closed)
# ---------------------------------------------------------------------------


class EventError(Exception):
    """Base class for every E-3 normalization error."""


class EventInputError(EventError, ValueError):
    """An E-3 argument is malformed (before any file is opened)."""


class EventFormatError(EventError, ValueError):
    """A stored filing differs from the frozen V§2 field list (plan §7)."""


class EventFirewallError(EventError):
    """An event would cross the sealed-reserve firewall (V§5, plan §3)."""


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class KeyCik:
    """One D4 ``security_key`` bound to a CIK by the E-2 identity map."""

    security_key: str
    cik: int


@dataclass(frozen=True)
class EventRow:
    """One normalized V§4 event fact.

    ``acceptance_utc`` / ``acceptance_et`` are timezone-aware ``datetime``
    values; ``k_session`` is the V§4 knowledge session; ``boundary_flag`` is
    the M1b §3 fact ``B_e``; ``report_period`` is the filing's ``reportDate``.
    """

    security_key: str
    cik: int
    accession: str
    form: str
    event_type: str
    acceptance_utc: datetime
    acceptance_et: datetime
    k_session: date
    boundary_flag: bool
    report_period: date | None


@dataclass(frozen=True)
class EventPanel:
    """The paths and counts written by :func:`write_event_panel`."""

    development_path: Path
    reserve_path: Path | None
    development_rows: int
    reserve_rows: int


def _coerce_key(key: Any) -> KeyCik:
    if isinstance(key, KeyCik):
        return key
    if isinstance(key, Mapping):
        return KeyCik(str(key["security_key"]), int(key["cik"]))
    raise EventInputError(f"unsupported key record {key!r}")


def _parse_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Knowledge timing (V§4)
# ---------------------------------------------------------------------------


def parse_acceptance_datetime(value: Any) -> datetime:
    """Parse a stored ``acceptanceDateTime`` into an aware UTC ``datetime``.

    The SEC field is ISO-8601 (normally ``...Z``).  A value that carries no
    offset is read as US Eastern time, because the frozen design states the
    field in ET; every offset-carrying value is converted to UTC.  A missing
    or malformed value fails closed (the plan §7 stop).
    """
    if isinstance(value, datetime):
        dt = value
    elif value is None:
        raise EventFormatError("acceptanceDateTime is missing")
    else:
        text = str(value).strip()
        if not text:
            raise EventFormatError("acceptanceDateTime is empty")
        normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
        try:
            dt = datetime.fromisoformat(normalized)
        except ValueError as exc:
            raise EventFormatError(
                f"acceptanceDateTime is not a timestamp: {value!r}"
            ) from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=EASTERN)
    return dt.astimezone(_UTC)


def to_eastern(value: datetime) -> datetime:
    """Convert an aware ``datetime`` to US Eastern time."""
    if value.tzinfo is None:
        raise EventInputError("a naive datetime has no US Eastern conversion")
    return value.astimezone(EASTERN)


def knowledge_session(
    acceptance_utc: datetime,
    calendar: TradingCalendar,
) -> date:
    """The V§4 knowledge session ``K(e)`` for one acceptance time.

    The session that contains the acceptance time, when it is strictly before
    16:00 ET on a trading session; otherwise the next trading session (after
    close, weekend, holiday or a non-session day).  The rule keys on 16:00 ET
    only: the frozen design does not shorten it for early-close sessions.
    """
    if acceptance_utc.tzinfo is None:
        raise EventInputError("acceptance_utc must be timezone-aware")
    eastern = to_eastern(acceptance_utc)
    day = eastern.date()
    if calendar.is_trading_day(day) and eastern.time() < MARKET_CLOSE_ET:
        return day
    return calendar.next_trading_day(day).date()


def boundary_flag(
    acceptance_et: datetime,
    calendar: TradingCalendar,
    early_closes: Mapping[date, time] | None = None,
) -> bool:
    """The M1b §3 boundary fact ``B_e``.

    ``B_e`` holds when the acceptance falls in
    ``[close - 15min, close + 15min)`` on a trading session, where ``close``
    is 16:00 ET (or the injected early-close time for that session).  The
    early-close times are an injected fact: no early-close table is present
    in the frozen E-1 raw layout or the trusted D3/D7c calendar, so the
    caller supplies one when it is available.
    """
    if acceptance_et.tzinfo is None:
        raise EventInputError("acceptance_et must be timezone-aware")
    day = acceptance_et.date()
    if not calendar.is_trading_day(day):
        return False
    close = (early_closes or {}).get(day, MARKET_CLOSE_ET)
    wall = acceptance_et.replace(tzinfo=None)
    centre = datetime.combine(day, close)
    lower = centre - timedelta(minutes=BOUNDARY_WINDOW_MINUTES)
    upper = centre + timedelta(minutes=BOUNDARY_WINDOW_MINUTES)
    return lower <= wall < upper


# ---------------------------------------------------------------------------
# Event typing (V§4)
# ---------------------------------------------------------------------------


def parse_items(value: Any) -> tuple[str, ...]:
    """Parse an 8-K ``items`` string (``"2.02,9.01"``) into exact tokens."""
    if value is None:
        return ()
    if isinstance(value, (list, tuple)):
        tokens = [str(item) for item in value]
    else:
        text = str(value).strip()
        if not text:
            return ()
        tokens = _ITEMS_SPLIT_RE.split(text)
    return tuple(token.strip() for token in tokens if token.strip())


def is_amendment(form: str) -> bool:
    """Whether a form is an amendment (``8-K/A``, ``10-K/A``, ...)."""
    return form.strip().upper().endswith("/A")


def classify_form(form: str, items: Any = None) -> str | None:
    """The V§4 event type a filing's form/items implies, or ``None``.

    Amendments never create an event.  An original 8-K creates ``EA_8K202``
    only when its ``items`` include 2.02.  A 10-Q/10-K is a candidate
    ``EA_PERIODIC_FALLBACK`` (the window rule is applied by the caller).  A
    6-K is ``FPI_6K`` and a 20-F/40-F is ``FPI_20F``.
    """
    form = form.strip().upper()
    amended = is_amendment(form)
    base = form[:-2] if amended else form
    if amended:
        return None
    if base == "8-K":
        return EA_8K202 if EA_8K_ITEM in parse_items(items) else None
    if base in PERIODIC_FALLBACK_FORMS:
        return EA_PERIODIC_FALLBACK
    if base in FPI_6K_FORMS:
        return FPI_6K
    if base in FPI_ANNUAL_FORMS:
        return FPI_20F
    return None


# ---------------------------------------------------------------------------
# Raw-layout readers (E-1 format, read-only)
# ---------------------------------------------------------------------------


def load_raw_submissions(raw_root: str | Path, cik: int) -> dict[str, Any] | None:
    """Read ``submissions/CIK##########.json`` bytes from an E-1 raw store."""
    path = Path(raw_root) / "submissions" / f"CIK{int(cik):010d}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def load_raw_filings(raw_root: str | Path, cik: int) -> list[dict[str, Any]]:
    """Every stored filing row for ``cik`` from the E-1 raw layout.

    The main submissions response and its ``files/*.json`` pagination are
    flattened with E-1's :func:`iter_filings`; duplicate accessions are
    dropped, keeping the first occurrence.
    """
    raw_root = Path(raw_root)
    seen: set[str] = set()
    rows: list[dict[str, Any]] = []

    def _extend(payload: Mapping[str, Any]) -> None:
        for row in iter_filings(payload):
            accession = str(row.get("accessionNumber") or "")
            if accession and accession in seen:
                continue
            if accession:
                seen.add(accession)
            rows.append(row)

    main = load_raw_submissions(raw_root, cik)
    if main is not None:
        _extend(main)
    files_dir = raw_root / "submissions" / "files"
    if files_dir.exists():
        prefix = f"CIK{int(cik):010d}-"
        for path in sorted(files_dir.glob("*.json")):
            if not path.name.startswith(prefix):
                continue
            _extend(json.loads(path.read_text(encoding="utf-8")))
    return rows


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------


def _filing_rows(payload: Any) -> list[dict[str, Any]]:
    if payload is None:
        return []
    if isinstance(payload, Mapping):
        return [dict(row) for row in iter_filings(payload)]
    return [dict(row) for row in payload]


def normalize_key(
    security_key: str,
    cik: int,
    filings: Sequence[Mapping[str, Any]],
    *,
    calendar: TradingCalendar,
    early_closes: Mapping[date, time] | None = None,
) -> list[EventRow]:
    """Normalize one key's stored filing rows into V§4 event facts.

    ``filings`` is the flattened per-filing row list for a single CIK (see
    :func:`load_raw_filings`).  Every recognized event form must carry an
    ``acceptanceDateTime``; a missing one fails closed (plan §7).
    """
    rows = [dict(row) for row in filings]

    # The original 8-K Item 2.02 filing dates, used only by the fallback
    # window rule.  ``filingDate`` is used here as a window bound; it is never
    # used as K(e).
    ea8k_dates: list[date] = []
    for row in rows:
        form = str(row.get("form") or "")
        items = parse_items(row.get("items"))
        if form.strip().upper() == "8-K" and EA_8K_ITEM in items:
            filed = _parse_date(row.get("filingDate"))
            if filed is not None:
                ea8k_dates.append(filed)

    events: list[EventRow] = []
    seen_accessions: set[str] = set()
    for row in rows:
        form = str(row.get("form") or "")
        items = parse_items(row.get("items"))
        event_type = classify_form(form, items)
        if event_type is None:
            continue
        accession = str(row.get("accessionNumber") or "")
        if accession and accession in seen_accessions:
            continue
        if accession:
            seen_accessions.add(accession)

        report_period = _parse_date(row.get("reportDate"))
        if event_type == EA_PERIODIC_FALLBACK:
            filed = _parse_date(row.get("filingDate"))
            if report_period is None or filed is None:
                # Without both window bounds the fallback rule is undecidable;
                # fail closed rather than silently emitting an event.
                raise EventFormatError(
                    "a periodic fallback filing lacks reportDate or filingDate"
                )
            if any(report_period <= d <= filed for d in ea8k_dates):
                continue

        acceptance_utc = parse_acceptance_datetime(row.get("acceptanceDateTime"))
        acceptance_et = to_eastern(acceptance_utc)
        k_session = knowledge_session(acceptance_utc, calendar)
        events.append(
            EventRow(
                security_key=str(security_key),
                cik=int(cik),
                accession=accession,
                form=form,
                event_type=event_type,
                acceptance_utc=acceptance_utc,
                acceptance_et=acceptance_et,
                k_session=k_session,
                boundary_flag=boundary_flag(
                    acceptance_et, calendar, early_closes
                ),
                report_period=report_period,
            )
        )
    return events


def normalize_events(
    key_ciks: Iterable[Any],
    submissions: Mapping[int, Any],
    *,
    calendar: TradingCalendar,
    early_closes: Mapping[date, time] | None = None,
    validate: bool = True,
) -> list[EventRow]:
    """Normalize a key→CIK map against stored submissions into event rows.

    ``submissions`` maps a CIK to either an E-1 submissions payload (a
    mapping, flattened with :func:`iter_filings`) or an already-flattened
    sequence of filing rows.  Rows are returned in the deterministic
    :func:`sort_event_rows` order.
    """
    events: list[EventRow] = []
    for raw_key in key_ciks:
        key = _coerce_key(raw_key)
        payload = submissions.get(int(key.cik))
        if payload is None:
            continue
        if validate and isinstance(payload, Mapping):
            validate_submission_payload(payload)
        events.extend(
            normalize_key(
                key.security_key,
                key.cik,
                _filing_rows(payload),
                calendar=calendar,
                early_closes=early_closes,
            )
        )
    return sort_event_rows(events)


# ---------------------------------------------------------------------------
# Deterministic output
# ---------------------------------------------------------------------------


def sort_event_rows(rows: Iterable[EventRow]) -> list[EventRow]:
    """The deterministic event-panel row order."""
    return sorted(
        rows,
        key=lambda row: (
            row.security_key,
            row.k_session,
            row.accession,
            row.event_type,
            row.form,
        ),
    )


def events_frame(rows: Iterable[EventRow]) -> pd.DataFrame:
    """The event panel as a DataFrame in the frozen schema and row order."""
    return _events_table(rows).to_pandas()


def _events_table(rows: Iterable[EventRow]) -> pa.Table:
    ordered = sort_event_rows(rows)
    data = {
        "security_key": [row.security_key for row in ordered],
        "cik": pa.array([row.cik for row in ordered], type=pa.int64()),
        "accession": [row.accession for row in ordered],
        "form": [row.form for row in ordered],
        "event_type": [row.event_type for row in ordered],
        "acceptance_utc": [
            row.acceptance_utc.isoformat() for row in ordered
        ],
        "acceptance_et": [row.acceptance_et.isoformat() for row in ordered],
        "k_session": pa.array(
            [row.k_session for row in ordered], type=pa.date32()
        ),
        "boundary_flag": pa.array(
            [row.boundary_flag for row in ordered], type=pa.bool_()
        ),
        "report_period": pa.array(
            [row.report_period for row in ordered], type=pa.date32()
        ),
    }
    return pa.table(data, schema=EVENT_SCHEMA)


def write_events_parquet(
    path: str | Path, rows: Iterable[EventRow]
) -> Path:
    """Write a deterministic, zstd-compressed event panel to ``path``."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(_events_table(rows), target, compression="zstd")
    return target


# ---------------------------------------------------------------------------
# Firewall split (V§5, plan §3)
# ---------------------------------------------------------------------------


def split_by_firewall(
    rows: Iterable[EventRow],
) -> tuple[list[EventRow], list[EventRow]]:
    """Split rows into ``(development, reserve)`` at ``2023-07-03``.

    A row whose ``k_session`` is at or after ``RESERVE_START`` is reserve
    data and may never be written to a development path.
    """
    development: list[EventRow] = []
    reserve: list[EventRow] = []
    for row in sort_event_rows(rows):
        if row.k_session >= RESERVE_START:
            reserve.append(row)
        else:
            development.append(row)
    return development, reserve


def write_event_panel(
    root: str | Path,
    acq_id: str,
    rows: Iterable[EventRow],
    *,
    development_relpath: str = "events.parquet",
) -> EventPanel:
    """Write the V§3 event panel with the V§5 firewall split.

    The development path holds only ``k_session < 2023-07-03`` rows; every
    reserve row is written to ``reserve_sealed/edgar-<acq_id>/events.parquet``
    and nowhere else.
    """
    root = Path(root)
    development_rows, reserve_rows = split_by_firewall(rows)

    development_path = root / development_relpath
    if PurePosixPath(development_relpath).parts and (
        "reserve_sealed" in PurePosixPath(development_relpath).parts
    ):
        raise EventFirewallError(
            f"development path may not contain reserve_sealed: "
            f"{development_relpath!r}"
        )
    write_events_parquet(development_path, development_rows)

    reserve_path: Path | None = None
    if reserve_rows:
        reserve_path = (
            root / "reserve_sealed" / f"edgar-{acq_id}" / "events.parquet"
        )
        write_events_parquet(reserve_path, reserve_rows)

    return EventPanel(
        development_path=development_path,
        reserve_path=reserve_path,
        development_rows=len(development_rows),
        reserve_rows=len(reserve_rows),
    )


# ---------------------------------------------------------------------------
# Development loader (fail closed; file-open audit)
# ---------------------------------------------------------------------------


class EventDevelopmentAccessor:
    """A fail-closed development reader for the E-3 event panel.

    It opens only the bound development file, records every path it opens in
    the injected ``audit`` list, refuses a request that touches the reserve
    boundary before opening anything, and refuses to return a file that
    contains a reserve row (V§5, plan §4 firewall).
    """

    def __init__(
        self,
        root: str | Path,
        *,
        development_relpath: str = "events.parquet",
        audit: list[Path] | None = None,
    ) -> None:
        self._root = Path(root)
        parts = PurePosixPath(development_relpath).parts
        if "reserve_sealed" in parts:
            raise EventFirewallError(
                f"refusing a reserve development binding: "
                f"{development_relpath!r}"
            )
        self._relpath = PurePosixPath(development_relpath)
        self._audit: list[Path] = audit if audit is not None else []

    @property
    def opened_paths(self) -> list[Path]:
        """Every path this accessor has opened (the file-open audit)."""
        return list(self._audit)

    def read(
        self,
        *,
        security_keys: Sequence[str] | None = None,
        start: date | str | None = None,
        end: date | str | None = None,
    ) -> pd.DataFrame:
        """Read the development event panel, failing closed on reserve data."""
        start_date = _parse_date(start) if start is not None else None
        end_date = _parse_date(end) if end is not None else None
        if start is not None and start_date is None:
            raise EventInputError(f"invalid start {start!r}")
        if end is not None and end_date is None:
            raise EventInputError(f"invalid end {end!r}")
        if end_date is not None and end_date >= RESERVE_START:
            raise EventFirewallError(
                f"development read end {end_date} touches the reserve "
                f"boundary {RESERVE_START}"
            )

        path = self._root / self._relpath
        self._audit.append(path)
        frame = pq.read_table(path).to_pandas()
        if frame.empty:
            return frame

        sessions = pd.to_datetime(frame["k_session"]).dt.date
        if bool((sessions >= RESERVE_START).any()):
            raise EventFirewallError(
                "the development event panel contains a sealed-reserve row"
            )

        if security_keys is not None:
            allowed = {str(key) for key in security_keys}
            frame = frame[frame["security_key"].isin(allowed)]
        if start_date is not None:
            frame = frame[sessions.loc[frame.index] >= start_date]
        if end_date is not None:
            frame = frame[sessions.loc[frame.index] <= end_date]
        return frame.reset_index(drop=True)


# ---------------------------------------------------------------------------
# Default calendar helper
# ---------------------------------------------------------------------------


def default_session_calendar(
    start: date | str = date(2015, 1, 2),
    end: date | str = date(2027, 12, 31),
) -> TradingCalendar:
    """The trusted D3/D7c session calendar, built read-only."""
    return build_nyse_calendar(start, end)
