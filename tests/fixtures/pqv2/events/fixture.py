"""Synthetic Dataset-v1.3 event-layer fixture for the PQ23-B tests.

Builds a tiny, complete ``derived/edgar-<acq_id>/`` root — ``manifest.json``,
``events.parquet`` and ``identity_map.parquet`` — from in-memory records.  No
production artifact is read, no network call is made and no credential value is
consulted.

The event/identity rows use exactly the frozen v1.3 real schema (V13-E2/E-3):
``events.parquet`` columns ``security_key, cik, accession, form, event_type,
acceptance_utc, acceptance_et, k_session, k_reason, boundary_flag,
report_period, acceptance_json_raw, json_discrepancy`` and the E-2
``identity_map.parquet`` columns.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from smart_beta.dataset_v1 import edgar_build as _build
from smart_beta.dataset_v1.edgar_events import EventRow, write_events_parquet
from smart_beta.dataset_v1.edgar_identity import IdentityRow, write_identity_map

DEFAULT_ACQ_ID = "e0e0e0e0e0e0e0e0"
DEFAULT_EV_ID = "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"


def business_days(start: date, count: int) -> list[date]:
    out: list[date] = []
    cursor = start
    while len(out) < count:
        if cursor.weekday() < 5:
            out.append(cursor)
        cursor += timedelta(days=1)
    return out


def make_identity(
    security_key: str,
    *,
    status: str = "MAPPED",
    cik: int | None = 1,
    interval_start: date | None = date(2015, 1, 2),
    interval_end: date | None = date(2023, 6, 30),
    audit_verdict: str = "NOT_SAMPLED",
    route: str | None = "TICKER",
    n_verified_candidates: int = 1,
) -> IdentityRow:
    return IdentityRow(
        security_key=security_key,
        cik=cik,
        status=status,
        route=route,
        interval_start=interval_start,
        interval_end=interval_end,
        n_verified_candidates=n_verified_candidates,
        audit_verdict=audit_verdict,
    )


def make_event(
    security_key: str,
    *,
    k_session: date | None,
    acceptance_et: datetime | None,
    event_type: str = "EA_8K202",
    form: str = "8-K",
    cik: int = 1,
    accession: str | None = None,
    boundary_flag: bool = False,
    acceptance_json_raw: str | None = None,
    json_discrepancy: str | None = None,
    k_reason: str | None = None,
    report_period: date | None = None,
    acceptance_utc: datetime | None = None,
) -> EventRow:
    return EventRow(
        security_key=security_key,
        cik=cik,
        accession=accession or f"{security_key}-{k_session or 'null'}",
        form=form,
        event_type=event_type,
        acceptance_utc=acceptance_utc,
        acceptance_et=acceptance_et,
        k_session=k_session,
        boundary_flag=boundary_flag,
        report_period=report_period,
        acceptance_json_raw=acceptance_json_raw,
        json_discrepancy=json_discrepancy,
        k_reason=k_reason,
    )


def write_event_root(
    root: Path | str,
    *,
    identities: Sequence[IdentityRow],
    events: Sequence[EventRow],
    acq_id: str = DEFAULT_ACQ_ID,
    ev_id: str = DEFAULT_EV_ID,
    package: str = "derived",
) -> tuple[str, str]:
    """Write a synthetic ``<package>/edgar-<acq_id>/`` root; return ids.

    ``package`` is ``"derived"`` for the development event layer (the default)
    or ``"reserve_sealed"`` for the v2.4 reserve fold's event layer.
    """

    root = Path(root)
    build_dir = root / package / f"edgar-{acq_id}"
    build_dir.mkdir(parents=True, exist_ok=True)
    write_identity_map(identities, build_dir / "identity_map.parquet")
    write_events_parquet(build_dir / "events.parquet", events)
    payload = _build.manifest_payload(acq_id, [], {"n_events": len(events)})
    manifest = {"schema": _build.SCHEMA, **payload, "ev_id": ev_id}
    (build_dir / "manifest.json").write_text(
        _json_dumps(manifest) + "\n", encoding="utf-8"
    )
    return acq_id, ev_id


def _json_dumps(obj: Any) -> str:
    import json

    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def tri_frame_from_levels(
    sessions: Sequence[date],
    levels: Mapping[str, Mapping[date, float]],
) -> Any:
    """Build a D7c-shaped TRI frame from *unlagged* levels.

    ``levels[key][d]`` is ``TRI(d)`` (the unlagged value).  Row ``sessions[i]``
    carries ``TRI(sessions[i-1])``; the first row is ``NaN``, exactly as the
    D7c bridge lag requires.
    """

    import pandas as pd

    index = pd.DatetimeIndex(pd.to_datetime(list(sessions)))
    frame = pd.DataFrame(index=index, columns=sorted(levels))
    for key, by_date in levels.items():
        for position in range(1, len(sessions)):
            previous = sessions[position - 1]
            if previous in by_date:
                frame.at[index[position], key] = float(by_date[previous])
    return frame.astype("float64")
