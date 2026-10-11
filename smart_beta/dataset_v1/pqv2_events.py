"""PQ23-B — P-Q v2.3 event inputs (EVENT_G primitives; M1b cause sources).

This module is the PQ23-B deliverable of the frozen P-Q v2.3 implementation
plan (``docs/discovery_dataset_v1_2_pq_v2_3_implementation_plan.md`` §2/§3) and
implements the event half of the frozen methodology
(``docs/discovery_dataset_v1_2_pq_v2_3_event_grammar.md`` §1/§2/§4) together
with the M1b cause semantics
(``docs/discovery_dataset_v1_3_m1b_event_sensitivity.md``).

It is a **new** module: the pinned P-Q v2 core (``pqv2_core``,
``trusted_inputs``, ``spec/*``) is imported read-only and never modified.

What it does
------------

* :func:`load_event_layer` — a fail-closed development loader for the v1.3
  ``derived/edgar-<acq_id>/`` artifacts.  It verifies the build ``ev_id``,
  reads ``events.parquet`` and ``identity_map.parquet`` and refuses any
  ``reserve_sealed`` path or any row with ``k_session >= 2023-07-03``.  It
  returns an :class:`EventLayer` carrying the per-key usable ``EA_8K202``
  events and the M1b cause sources.
* :func:`build_event_inputs` — the §2 event primitives
  (``event_ear_k_kp1``, ``event_days_since_ea`` and
  ``event_recent_ea_w<W>``) as Phase-6 :class:`TrustedInput` frames, for the
  requested roles and scenario (``K0`` / ``K1``).

The binding conventions (plan §2)
---------------------------------

* **Bridge lag (the ADM-13 lesson):** the value at frame row ``f`` uses only
  information dated ``<= date(f-1)``, the same as D7c.  An event ``e`` is
  observed at row ``f`` iff ``K(e) + 1 <= date(f-1)``.
* **``ear``:** for ``e* = TRI_dated(K+1)/TRI_dated(K-1) - 1``, where
  ``TRI_dated(d)`` is read from the D7c TRI frame **at the row whose previous
  label is ``d``** (the E-4-R2 rule).  The row label is *not* the TRI date.
* **``days_since_ea``:** the trading-session count from ``K(e*)`` to
  ``date(f-1)``, inclusive of ``date(f-1)`` and exclusive of ``K(e*)``; it is
  always ``>= 1``.
* **Lookback:** ``e*`` must satisfy ``K(e*) >=`` the session 504 trading
  sessions before ``date(f-1)``; otherwise every primitive at row ``f`` is
  ``NaN``.
* **Scenarios:** ``K0`` is the v1.3 ``k_session``.  ``K1`` shifts the session
  by one (the next trading session) **only** for a ``B_e`` event, where
  ``B_e`` holds iff the index-header ``acceptance_et`` lies in
  ``[15:45:00, 16:00:00)`` America/New_York on a trusted session (M1b §3, the
  asymmetric pre-close boundary).  The recorded v1.3 ``boundary_flag`` is the
  symmetric ±15-minute fact and is *not* ``B_e``.

Nothing here reads the reserve, holdout, ``raw/`` or ``dsc_evidence``; the
development path is the only path opened, and no network call is made.
"""

from __future__ import annotations

import bisect
import json
import math
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from smart_beta.dataset_v1.pqv2_core import (
    DEVELOPMENT_FOLD,
    Fold,
    PQv2InputError,
    RESERVE_START,
)
from smart_beta.spec.engine import TrustedInput
from smart_beta.spec.requirements import (
    DataCapability,
    Frequency,
    ObservationPeriod,
    RevisionPolicy,
    Unit,
)

__all__ = [
    "EventLayerError",
    "EventFirewallError",
    "EventCause",
    "EVENT_CAUSE_PRECEDENCE",
    "EventRecord",
    "NullTimeEvent",
    "EventLayer",
    "load_event_layer",
    "build_event_inputs",
    "event_semantic_id",
    "recent_ea_semantic_id",
    "DEFAULT_EVENT_ROLES",
    "EVENT_EA_KKP1",
    "EVENT_DAYS_SINCE_EA",
    "EVENT_RECENT_EA_PREFIX",
    "RECENT_EA_MIN",
    "RECENT_EA_MAX",
    "LOOKBACK_SESSIONS",
    "BOUNDARY_START",
    "BOUNDARY_END",
    "SCENARIO_K0",
    "SCENARIO_K1",
    "SCENARIOS",
    "EA_8K202",
    "EA_PERIODIC_FALLBACK",
    "FPI_6K",
    "FPI_20F",
    "EXCLUDED_EVENT_TYPES",
    "is_preclose_b_e",
]

# ---------------------------------------------------------------------------
# Frozen literals (plan §2/§3, grammar §1/§2)
# ---------------------------------------------------------------------------

#: §2 frozen semantic ids.
EVENT_EA_KKP1 = "event_ear_k_kp1"
EVENT_DAYS_SINCE_EA = "event_days_since_ea"
EVENT_RECENT_EA_PREFIX = "event_recent_ea_w"

#: §2 ``recent_ea_w<W>`` bounds and the 504-session lookback.
RECENT_EA_MIN = 2
RECENT_EA_MAX = 504
LOOKBACK_SESSIONS = 504

#: M1b §3 asymmetric pre-close boundary (America/New_York wall clock).
BOUNDARY_START = time(15, 45)
BOUNDARY_END = time(16, 0)

#: The two scenario names (grammar §3, plan §2).
SCENARIO_K0 = "K0"
SCENARIO_K1 = "K1"
SCENARIOS = (SCENARIO_K0, SCENARIO_K1)

#: The M1b §1 event set: original 8-K Item 2.02 events, plus the v1.3 types
#: that are deliberately excluded (fallback and the two FPI types).
EA_8K202 = "EA_8K202"
EA_PERIODIC_FALLBACK = "EA_PERIODIC_FALLBACK"
FPI_6K = "FPI_6K"
FPI_20F = "FPI_20F"
EXCLUDED_EVENT_TYPES = (EA_PERIODIC_FALLBACK, FPI_6K, FPI_20F)

#: The observed index-header boundary window start used by V13-E2/E-4 for the
#: ``EVENT_TIME_UNAVAILABLE`` typed reason.
EVENT_TIME_UNAVAILABLE = "EVENT_TIME_UNAVAILABLE"

#: The V13-E2 scope floor for a filing-date proxy.
FILING_DATE_FLOOR = date(2015, 1, 2)

#: The v1.3 event panel column order (plan, V13-E2/E-3).
EVENT_COLUMNS = (
    "security_key",
    "cik",
    "accession",
    "form",
    "event_type",
    "acceptance_utc",
    "acceptance_et",
    "k_session",
    "k_reason",
    "boundary_flag",
    "report_period",
    "acceptance_json_raw",
    "json_discrepancy",
)

#: The identity-map columns this loader consumes.
IDENTITY_COLUMNS = (
    "security_key",
    "status",
    "interval_start",
    "interval_end",
    "audit_verdict",
)

_EASTERN = ZoneInfo("America/New_York")

#: The default roles: the two parameter-free §2 primitives.
DEFAULT_EVENT_ROLES: tuple[str, ...] = ("ear", "days_since_ea")


class EventLayerError(PQv2InputError):
    """A fail-closed v1.3 event-layer loader / builder error."""


class EventFirewallError(EventLayerError):
    """A reserve-bearing path or row reached a development event loader."""


class EventCause:
    """The M1b event cause literals (M1b §2, grammar §4)."""

    IDENTITY_UNMAPPED = "PQV2_EVENT_IDENTITY_UNMAPPED"
    IDENTITY_CONTRADICTED = "PQV2_EVENT_IDENTITY_CONTRADICTED"
    SOURCE_EXCLUDED = "PQV2_EVENT_SOURCE_EXCLUDED"
    TIME_UNAVAILABLE = "PQV2_EVENT_TIME_UNAVAILABLE"
    RESERVE = "PQV2_EVENT_RESERVE"
    ALL: tuple[str, ...] = (
        IDENTITY_CONTRADICTED,
        IDENTITY_UNMAPPED,
        SOURCE_EXCLUDED,
        TIME_UNAVAILABLE,
        RESERVE,
    )


#: M1b event-cause precedence (highest first; RESERVE is the defensive firewall
#: cause and is never produced by a correctly loaded development layer).
EVENT_CAUSE_PRECEDENCE: tuple[str, ...] = (
    EventCause.RESERVE,
    EventCause.IDENTITY_CONTRADICTED,
    EventCause.IDENTITY_UNMAPPED,
    EventCause.SOURCE_EXCLUDED,
    EventCause.TIME_UNAVAILABLE,
)

#: Identity-map statuses that trigger ``PQV2_EVENT_IDENTITY_UNMAPPED``.
_UNMAPPED_STATUSES = frozenset(
    {"AMBIGUOUS", "NO_FILINGS_IN_INTERVAL", "NO_CANDIDATE"}
)
_STATUS_FOREIGN_FILER = "FOREIGN_FILER"
_STATUS_MAPPED = "MAPPED"
_VERDICT_CONTRADICTED = "CONTRADICTED"


# ---------------------------------------------------------------------------
# Scalar coercion helpers
# ---------------------------------------------------------------------------


def _as_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    if isinstance(value, datetime):
        if pd.isna(value):
            return None
        return value.date()
    if isinstance(value, date):
        return value
    parsed = pd.Timestamp(value)
    if pd.isna(parsed):
        return None
    return parsed.date()


def _as_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    if isinstance(value, datetime):
        if pd.isna(value):
            return None
        return value
    text = str(value).strip()
    if not text:
        return None
    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def _as_str(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    text = str(value)
    return text if text else None


def _as_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_bool(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, float) and math.isnan(value):
        return False
    try:
        if pd.isna(value):
            return False
    except (TypeError, ValueError):  # pragma: no cover - defensive
        pass
    return bool(value)


def _acceptance_json_date(value: Any) -> date | None:
    """The filing-date proxy of the recorded submissions-JSON timestamp.

    V13-E2 keeps ``acceptance_json_raw`` as a recorded fact and forbids its use
    as a time source; M1b uses **only** its date, as a filing-date proxy for
    the ``EVENT_TIME_UNAVAILABLE`` scope test.  An absent or unparseable value
    yields ``None``.
    """

    text = _as_str(value)
    if text is None:
        return None
    parsed = _as_datetime(text)
    if parsed is None:
        return None
    return parsed.date()


# ---------------------------------------------------------------------------
# B_e (the asymmetric pre-close boundary, M1b §3)
# ---------------------------------------------------------------------------


def is_preclose_b_e(
    acceptance_et: datetime | None, trusted_sessions: Iterable[date]
) -> bool:
    """``B_e`` per M1b §3: ``acceptance_et`` in ``[15:45, 16:00)`` ET on a
    trusted session.

    The accepted wall clock is evaluated in America/New_York directly from the
    recorded index-header time.  The recorded v1.3 ``boundary_flag`` (symmetric
    ±15 minutes) is deliberately **not** consulted.
    """

    if acceptance_et is None:
        return False
    if acceptance_et.tzinfo is None:
        eastern = acceptance_et.replace(tzinfo=_EASTERN)
    else:
        eastern = acceptance_et.astimezone(_EASTERN)
    day = eastern.date()
    if day not in trusted_sessions:
        return False
    wall = eastern.time()
    return BOUNDARY_START <= wall < BOUNDARY_END


# ---------------------------------------------------------------------------
# Role <-> semantic id
# ---------------------------------------------------------------------------


def recent_ea_semantic_id(window: int) -> str:
    """The frozen §2 semantic id for ``recent_ea_w<W>`` (``2 <= W <= 504``)."""

    if isinstance(window, bool) or not isinstance(window, int):
        raise EventLayerError(f"recent_ea window must be an integer, got {window!r}")
    if not RECENT_EA_MIN <= window <= RECENT_EA_MAX:
        raise EventLayerError(
            f"recent_ea window {window} is outside "
            f"[{RECENT_EA_MIN}, {RECENT_EA_MAX}]"
        )
    return f"{EVENT_RECENT_EA_PREFIX}{window}"


def event_semantic_id(role: Any) -> str:
    """Resolve one requested role to its frozen §2 semantic id.

    Accepted role spellings: ``"ear"``, ``"days_since_ea"``, an integer
    ``W`` (``recent_ea_w<W>``), or any of the semantic ids themselves.
    """

    if isinstance(role, bool) or isinstance(role, float):
        raise EventLayerError(f"unsupported event role {role!r}")
    if isinstance(role, int):
        return recent_ea_semantic_id(role)
    if isinstance(role, str):
        text = role.strip()
        if text in ("ear", EVENT_EA_KKP1):
            return EVENT_EA_KKP1
        if text in ("days_since_ea", EVENT_DAYS_SINCE_EA):
            return EVENT_DAYS_SINCE_EA
        if text.startswith(EVENT_RECENT_EA_PREFIX):
            suffix = text[len(EVENT_RECENT_EA_PREFIX) :]
        elif text.startswith("recent_ea_w"):
            suffix = text[len("recent_ea_w") :]
        elif text.startswith("recent_ea(") and text.endswith(")"):
            suffix = text[len("recent_ea(") : -1]
        else:
            suffix = None
        if suffix is not None:
            try:
                window = int(suffix)
            except ValueError as exc:
                raise EventLayerError(
                    f"unsupported recent_ea role {role!r}"
                ) from exc
            return recent_ea_semantic_id(window)
    raise EventLayerError(f"unsupported event role {role!r}")


def _resolve_roles(roles: Sequence[Any] | None) -> tuple[str, ...]:
    requested = DEFAULT_EVENT_ROLES if roles is None else tuple(roles)
    resolved: list[str] = []
    for role in requested:
        semantic = event_semantic_id(role)
        if semantic not in resolved:
            resolved.append(semantic)
    return tuple(resolved)


# ---------------------------------------------------------------------------
# Layer records
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EventRecord:
    """One usable ``EA_8K202`` development event (K(e) present).

    ``k_session`` is the v1.3 ``K0`` knowledge session.  ``acceptance_et`` is
    the recorded index-header time (America/New_York); ``b_e`` is *not* stored
    here because the trusted-session membership is supplied to the builder.
    """

    security_key: str
    cik: int | None
    accession: str
    form: str
    event_type: str
    k_session: date
    acceptance_et: datetime | None
    acceptance_json_raw: str | None
    filing_date: date | None
    boundary_flag: bool


@dataclass(frozen=True)
class NullTimeEvent:
    """One in-scope ``EA_8K202`` with a null ``k_session`` (M1b cause source)."""

    security_key: str
    accession: str
    filing_date: date | None
    raw_acceptance_json: str | None


@dataclass(frozen=True)
class EventLayer:
    """The deterministic per-key v1.3 event layer + M1b cause sources."""

    acq_id: str
    ev_id: str
    keys: tuple[str, ...]
    status_by_key: Mapping[str, str]
    verdict_by_key: Mapping[str, str]
    interval_by_key: Mapping[str, tuple[date | None, date | None]]
    events_by_key: Mapping[str, tuple[EventRecord, ...]]
    null_time_by_key: Mapping[str, tuple[NullTimeEvent, ...]]
    reserve_incident: bool = False

    def __post_init__(self) -> None:
        for name in (
            "status_by_key",
            "verdict_by_key",
            "interval_by_key",
            "events_by_key",
            "null_time_by_key",
        ):
            object.__setattr__(
                self, name, MappingProxyType(dict(getattr(self, name)))
            )

    def key_causes(self, security_key: str) -> tuple[str, ...]:
        """The key-scoped M1b causes in precedence order.

        ``TIME_UNAVAILABLE`` is excluded here because M1b scopes it to the
        cell's event lookback; :meth:`time_unavailable` exposes its source.
        """

        status = self.status_by_key.get(security_key)
        if status is None:
            raise EventLayerError(f"unknown security_key {security_key!r}")
        causes: list[str] = []
        if self.verdict_by_key.get(security_key) == _VERDICT_CONTRADICTED:
            causes.append(EventCause.IDENTITY_CONTRADICTED)
        if status in _UNMAPPED_STATUSES:
            causes.append(EventCause.IDENTITY_UNMAPPED)
        if status == _STATUS_FOREIGN_FILER:
            causes.append(EventCause.SOURCE_EXCLUDED)
        if self.reserve_incident:
            causes.insert(0, EventCause.RESERVE)
        return tuple(causes)

    def time_unavailable(self, security_key: str) -> tuple[NullTimeEvent, ...]:
        """The key's in-scope null-time ``EA_8K202`` events (M1b source)."""

        return tuple(self.null_time_by_key.get(security_key, ()))

    def time_unavailable_in_window(
        self, security_key: str, start: date, end: date
    ) -> tuple[NullTimeEvent, ...]:
        """The in-scope null-time events whose filing-date proxy is in
        ``[start, end]``.

        A ``None`` filing-date proxy is conservatively retained (it can never
        be shown to be outside the window), so a null-time event is never
        silently dropped.
        """

        out: list[NullTimeEvent] = []
        for event in self.null_time_by_key.get(security_key, ()):
            if event.filing_date is None:
                out.append(event)
            elif start <= event.filing_date <= end:
                out.append(event)
        return tuple(out)


# ---------------------------------------------------------------------------
# Development loader
# ---------------------------------------------------------------------------


def _refuse_reserve_path(path: Path, fold: Fold = DEVELOPMENT_FOLD) -> None:
    if fold.is_development and "reserve_sealed" in path.parts:
        raise EventFirewallError(
            f"development event loader refuses a reserve path: {path}"
        )


def _verify_manifest(build_dir: Path, acq_id: str, ev_id: str) -> Mapping[str, Any]:
    manifest_path = build_dir / "manifest.json"
    if not manifest_path.is_file():
        raise EventLayerError(f"event build manifest not found: {manifest_path}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EventLayerError("event build manifest is not valid JSON") from exc
    if not isinstance(manifest, dict):
        raise EventLayerError("event build manifest is not a JSON object")
    if manifest.get("ev_id") != ev_id:
        raise EventLayerError(
            "event build manifest ev_id does not match the requested ev_id"
        )
    if manifest.get("acq_id") not in (None, acq_id):
        raise EventLayerError(
            "event build manifest acq_id does not match the requested acq_id"
        )
    return manifest


def _in_scope(
    filing_date: date | None,
    interval: tuple[date | None, date | None],
) -> bool:
    """M1b scope for a null-time event.

    ``filed >= 2015-01-02`` and inside the key's D4 interval, using the
    recorded JSON date as a filing-date proxy only.  An absent proxy is
    conservatively in scope (never silently skipped).
    """

    if filing_date is not None and filing_date < FILING_DATE_FLOOR:
        return False
    start, end = interval
    if filing_date is not None:
        if start is not None and filing_date < start:
            return False
        if end is not None and filing_date > end:
            return False
    return True


def load_event_layer(
    root: Path | str,
    *,
    acq_id: str,
    ev_id: str,
    fold: Fold = DEVELOPMENT_FOLD,
) -> EventLayer:
    """Load and verify the v1.3 event layer for the declared fold (plan §3).

    Under the default development fold it verifies
    ``derived/edgar-<acq_id>/manifest.json`` carries ``ev_id``, reads
    ``events.parquet`` and ``identity_map.parquet`` and refuses any
    ``reserve_sealed`` path or any row with ``k_session >= 2023-07-03``
    (byte-identical to v2.3).

    Under a reserve fold it reads ``reserve_sealed/edgar-<acq_id>/`` and admits
    the reserve rows with ``K(e) <= E``; a row with ``K(e) > E`` is a firewall
    incident.  The reserve package is opened **only** under a reserve fold
    (firewall §2.2).
    """

    if not isinstance(acq_id, str) or not acq_id:
        raise EventLayerError("acq_id must be a non-empty string")
    if not isinstance(ev_id, str) or not ev_id:
        raise EventLayerError("ev_id must be a non-empty string")
    if not isinstance(fold, Fold):
        raise EventLayerError("fold must be a Fold")

    root_path = Path(root)
    package = "derived" if fold.is_development else "reserve_sealed"
    build_dir = root_path / package / f"edgar-{acq_id}"
    _refuse_reserve_path(root_path, fold)
    _refuse_reserve_path(build_dir, fold)

    _verify_manifest(build_dir, acq_id, ev_id)

    events_path = build_dir / "events.parquet"
    identity_path = build_dir / "identity_map.parquet"
    if not events_path.is_file():
        raise EventLayerError(f"event panel not found: {events_path}")
    if not identity_path.is_file():
        raise EventLayerError(f"identity map not found: {identity_path}")

    events_frame = pd.read_parquet(events_path)
    identity_frame = pd.read_parquet(identity_path)

    missing = [name for name in EVENT_COLUMNS if name not in events_frame.columns]
    if missing:
        raise EventLayerError(f"event panel lacks columns {missing}")
    missing = [name for name in IDENTITY_COLUMNS if name not in identity_frame.columns]
    if missing:
        raise EventLayerError(f"identity map lacks columns {missing}")

    # Firewall §2.2 / V§5: a development load may carry no reserve-dated
    # K(e), and any fold refuses a K(e) beyond its end E.  The development
    # branch keeps the exact v2.3 rule (>= RESERVE_START), so the default path
    # is byte-identical.
    incidents: list[date] = []
    for value in events_frame["k_session"]:
        session = _as_date(value)
        if session is None:
            continue
        if fold.is_development:
            if session >= RESERVE_START:
                incidents.append(session)
        elif session > fold.end:
            incidents.append(session)
    if incidents:
        raise EventFirewallError(
            "event panel contains a sealed-reserve or beyond-fold-end row "
            f"(k_session >= {RESERVE_START if fold.is_development else fold.end})"
        )

    status_by_key: dict[str, str] = {}
    verdict_by_key: dict[str, str] = {}
    interval_by_key: dict[str, tuple[date | None, date | None]] = {}
    for record in identity_frame.to_dict("records"):
        key = _as_str(record.get("security_key"))
        if key is None:
            raise EventLayerError("identity map row lacks security_key")
        if key in status_by_key:
            raise EventLayerError(f"identity map repeats security_key {key!r}")
        status_by_key[key] = str(record["status"])
        verdict_by_key[key] = str(record.get("audit_verdict") or "")
        interval_by_key[key] = (
            _as_date(record.get("interval_start")),
            _as_date(record.get("interval_end")),
        )

    usable: dict[str, list[EventRecord]] = {}
    null_time: dict[str, list[NullTimeEvent]] = {}
    for record in events_frame.to_dict("records"):
        key = _as_str(record.get("security_key"))
        if key is None or key not in status_by_key:
            raise EventLayerError(
                f"event panel references unknown security_key {key!r}"
            )
        event_type = _as_str(record.get("event_type")) or ""
        if not _is_ea_8k202(event_type):
            continue
        k_session = _as_date(record.get("k_session"))
        filing_date = _acceptance_json_date(record.get("acceptance_json_raw"))
        if k_session is None:
            if is_in_scope_null_time(filing_date, interval_by_key[key]):
                null_time.setdefault(key, []).append(
                    NullTimeEvent(
                        security_key=key,
                        accession=str(record.get("accession") or ""),
                        filing_date=filing_date,
                        raw_acceptance_json=_as_str(
                            record.get("acceptance_json_raw")
                        ),
                    )
                )
            continue
        usable.setdefault(key, []).append(
            EventRecord(
                security_key=key,
                cik=_as_int(record.get("cik")),
                accession=str(record.get("accession") or ""),
                form=str(record.get("form") or ""),
                event_type=event_type,
                k_session=k_session,
                acceptance_et=_as_datetime(record.get("acceptance_et")),
                acceptance_json_raw=_as_str(record.get("acceptance_json_raw")),
                filing_date=filing_date,
                boundary_flag=_as_bool(record.get("boundary_flag")),
            )
        )

    keys = tuple(sorted(status_by_key))
    return EventLayer(
        acq_id=acq_id,
        ev_id=ev_id,
        keys=keys,
        status_by_key=status_by_key,
        verdict_by_key=verdict_by_key,
        interval_by_key=interval_by_key,
        events_by_key={
            key: tuple(sorted(values, key=_event_sort_key))
            for key, values in usable.items()
        },
        null_time_by_key={
            key: tuple(sorted(values, key=_null_time_sort_key))
            for key, values in null_time.items()
        },
    )


def _is_ea_8k202(event_type: str) -> bool:
    """The M1b §1 event set: original 8-K Item 2.02 events only."""

    return event_type == EA_8K202


def is_in_scope_null_time(
    filing_date: date | None,
    interval: tuple[date | None, date | None],
) -> bool:
    """Public spelling of the M1b null-time scope test (see :func:`_in_scope`)."""

    return _in_scope(filing_date, interval)


def _event_sort_key(event: EventRecord) -> tuple[Any, ...]:
    return (
        event.k_session,
        event.acceptance_et or datetime.min.replace(tzinfo=_EASTERN),
        event.accession,
    )


def _null_time_sort_key(event: NullTimeEvent) -> tuple[Any, ...]:
    return (event.filing_date or date.min, event.accession)


# ---------------------------------------------------------------------------
# D7c TRI bridge-lag lookup
# ---------------------------------------------------------------------------


def _predecessor_labels(tri_frame: pd.DataFrame) -> dict[date, Any]:
    """Map ``prev(row label) -> row label`` for the D7c TRI frame.

    The D7c contract is "the row dated ``d`` carries the observation from
    session ``prev(d)``", so ``TRI_dated(d)`` lives at the row whose previous
    label is ``d`` (the E-4-R2 rule).  The map is built from the frame's own
    label order, so a frame whose labels are offset from the primitive dates is
    handled correctly.
    """

    labels = list(tri_frame.index)
    out: dict[date, Any] = {}
    for position in range(1, len(labels)):
        previous = _as_date(labels[position - 1])
        label = labels[position]
        if previous is None:
            continue
        out[previous] = label
    return out


def _tri_dated_value(
    tri_frame: pd.DataFrame, predecessor: Mapping[date, Any], tri_date: date, key: str
) -> float | None:
    """``TRI_dated(tri_date)`` for one key, or ``None`` when unavailable."""

    row_label = predecessor.get(tri_date)
    if row_label is None or key not in tri_frame.columns:
        return None
    value = tri_frame.at[row_label, key]
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


# ---------------------------------------------------------------------------
# Sessions / frames
# ---------------------------------------------------------------------------


def _normalise_sessions(sessions: Sequence[Any]) -> list[date]:
    parsed: list[date] = []
    for item in sessions:
        value = _as_date(item)
        if value is None:
            raise EventLayerError(f"session {item!r} is not a calendar date")
        parsed.append(value)
    return sorted(set(parsed))


def _event_capability(semantic_id: str, history: int) -> DataCapability:
    if semantic_id == EVENT_EA_KKP1:
        period = ObservationPeriod.PERIOD
        units: Unit | None = Unit.FRACTION
    elif semantic_id == EVENT_DAYS_SINCE_EA:
        period = ObservationPeriod.INSTANT
        units = Unit.COUNT
    else:
        period = ObservationPeriod.INSTANT
        units = Unit.COUNT
    return DataCapability(
        semantic_id=semantic_id,
        frequency=Frequency.DAILY,
        observation_period=period,
        units=units,
        history=history,
        has_knowledge_date=True,
        has_positive_vintage_identity=False,
        revision_policies=frozenset({RevisionPolicy.POINT_IN_TIME}),
    )


# ---------------------------------------------------------------------------
# The builder
# ---------------------------------------------------------------------------


def build_event_inputs(
    layer: EventLayer,
    tri_frame: pd.DataFrame,
    sessions: Sequence[Any],
    *,
    scenario: str,
    roles: Sequence[Any] | None = None,
) -> dict[str, TrustedInput]:
    """Build the requested §2 event primitives as Phase-6 ``TrustedInput``s.

    Parameters
    ----------
    layer:
        An :class:`EventLayer` from :func:`load_event_layer`.
    tri_frame:
        The D7c ``total_return_index`` wide frame (row ``d`` carries
        ``TRI(prev(d))``).  The returned frames carry **exactly** this frame's
        columns, in order: a key absent from ``layer`` is NaN in every
        primitive, and a mapped key keeps its value bit-for-bit.
    sessions:
        The trusted-session grid; the returned frames are indexed by it.
    scenario:
        ``K0`` (the v1.3 ``k_session``) or ``K1`` (the next trading session for
        a ``B_e`` event, unchanged otherwise).
    roles:
        The requested roles (``"ear"``, ``"days_since_ea"``, an integer
        ``recent_ea_w<W>`` window, or any §2 semantic id).  Defaults to the two
        parameter-free primitives.

    Returns ``{semantic_id: TrustedInput}`` in the order the roles were
    requested.
    """

    if scenario not in SCENARIOS:
        raise EventLayerError(f"unknown scenario {scenario!r}")
    if not isinstance(layer, EventLayer):
        raise EventLayerError("layer must be an EventLayer")
    if not isinstance(tri_frame, pd.DataFrame):
        raise EventLayerError("tri_frame must be a pandas DataFrame")

    session_list = _normalise_sessions(sessions)
    session_index = pd.DatetimeIndex(pd.to_datetime(session_list))
    position = {session: index for index, session in enumerate(session_list)}
    session_set = frozenset(session_list)
    semantic_ids = _resolve_roles(roles)

    predecessor = _predecessor_labels(tri_frame)
    # PQ-EV: the event frames live on the supplied D7c grid (the columns of
    # ``tri_frame``), never on ``layer.keys``.  A key absent from the layer is
    # NaN in every primitive; a mapped key keeps its value bit-for-bit.  This
    # is what lets a mixed EVENT_SIMPLE expression share one grid with its
    # price term.
    keys = list(tri_frame.columns)
    n_rows = len(session_list)

    days = np.full((n_rows, len(keys)), np.nan, dtype="float64")
    ears = np.full((n_rows, len(keys)), np.nan, dtype="float64")

    for column, key in enumerate(keys):
        events = layer.events_by_key.get(key, ())
        # Resolve this scenario's K for every usable event and index by the
        # session position on the trusted grid (events off the grid are
        # unusable in this scenario).
        k_positions: dict[int, date] = {}
        for event in events:
            k_session = _scenario_k(
                event, scenario, session_list, position, session_set
            )
            if k_session is None:
                continue
            k_position = position.get(k_session)
            if k_position is None:
                continue
            k_positions.setdefault(k_position, k_session)
        if not k_positions:
            continue
        ordered_positions = sorted(k_positions)
        for row in range(1, n_rows):
            previous_position = row - 1
            lookback_floor = max(0, previous_position - LOOKBACK_SESSIONS)
            # The latest observed event: K(e) + 1 <= date(f-1).
            index = _latest_observed(ordered_positions, previous_position)
            if index is None:
                continue
            k_position = ordered_positions[index]
            if k_position < lookback_floor:
                continue
            k_session = k_positions[k_position]
            days[row, column] = float(previous_position - k_position)
            if k_position - 1 < 0 or k_position + 1 >= n_rows:
                continue
            low = _tri_dated_value(
                tri_frame, predecessor, session_list[k_position - 1], key
            )
            high = _tri_dated_value(
                tri_frame, predecessor, session_list[k_position + 1], key
            )
            if low is None or high is None or low <= 0.0:
                continue
            ears[row, column] = high / low - 1.0

    days_frame = pd.DataFrame(days, index=session_index, columns=tri_frame.columns)
    ear_frame = pd.DataFrame(ears, index=session_index, columns=tri_frame.columns)

    built: dict[str, TrustedInput] = {}
    for semantic_id in semantic_ids:
        if semantic_id == EVENT_EA_KKP1:
            frame = ear_frame
        elif semantic_id == EVENT_DAYS_SINCE_EA:
            frame = days_frame
        else:
            window = int(semantic_id[len(EVENT_RECENT_EA_PREFIX) :])
            values = days_frame.to_numpy(dtype="float64", copy=True)
            indicator = np.where(
                np.isnan(values), np.nan, (values <= float(window)).astype("float64")
            )
            frame = pd.DataFrame(
                indicator, index=session_index, columns=tri_frame.columns
            )
        built[semantic_id] = TrustedInput(
            values=frame,
            capability=_event_capability(semantic_id, n_rows),
        )
    return built


def _latest_observed(ordered_positions: Sequence[int], previous_position: int) -> int | None:
    """Index of the latest event observed at ``previous_position`` (``K+1 <=``).

    ``ordered_positions`` is ascending.  Returns the index of the largest
    position with ``position + 1 <= previous_position``, or ``None``.
    """

    index = bisect.bisect_right(ordered_positions, previous_position - 1) - 1
    if index < 0:
        return None
    return index


def _scenario_k(
    event: EventRecord,
    scenario: str,
    session_list: Sequence[date],
    position: Mapping[date, int],
    session_set: frozenset[date],
) -> date | None:
    """The scenario knowledge session ``K`` for one event."""

    if scenario == SCENARIO_K0:
        return event.k_session
    # K1: only a B_e event moves; every other event is unchanged (plan §2).
    if not is_preclose_b_e(event.acceptance_et, session_set):
        return event.k_session
    event_position = position.get(event.k_session)
    if event_position is None or event_position + 1 >= len(session_list):
        return None
    return session_list[event_position + 1]
