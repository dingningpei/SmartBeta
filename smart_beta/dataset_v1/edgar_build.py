"""E-4 — Dataset v1.3 EDGAR build: integration, gates and deterministic manifest.

Authority:

* the frozen design ``docs/discovery_dataset_v1_3_edgar_events_design.md``
  (``c9daa54b…b269``), "V§n" below;
* the frozen plan ``docs/discovery_dataset_v1_3_implementation_plan.md`` —
  §1 row E-4, §2 security, §3 interfaces, §4 gates/determinism tests (owner
  E-4), §5 mutations (prefix ``E4``), §7 stops, and the binding §8 IM-P
  evidence-sufficiency rule.

The module is the offline integration layer of Dataset v1.3.  It joins the
E-2 identity map and the E-3 event panel to the D4 keys and evaluates the
three frozen gates:

* **IM-P** (V§3, plan §8) — aggregated from the E-2 ``audit_verdict`` column.
  IM-P is evaluated only on the keys with symbol evidence and an undersized
  evidence base is ``NOT_CERTIFIED`` rather than a weak ``PASS``;
* **EV-C** (V§4) — among MAPPED domestic keys, the median ``EA_8K202`` events
  per listed year in development is ``>= 3.0`` and at least ``80%`` of mapped
  domestic key-years carry ``>= 3`` ``EA_8K202`` events;
* **EV-T** (V§4) — for a seeded sample of 200 ``EA_8K202`` development
  events, ``|return|`` on ``K(e)`` exceeds the security's own trailing median
  ``|return|`` in ``>= 60%`` of cases.

EV-T reads development returns **only** through :class:`D7cDevelopmentReturns`,
a thin, read-only, fail-closed view over the existing D7c ``TrustedInput``
(the :func:`~smart_beta.dataset_v1.trusted_inputs.load_trusted_inputs`
loader).  It reads no factor and no long-short outcome, and it refuses any
session at or after the sealed reserve boundary ``2023-07-03`` (V§5).

The build writes the V§3/V§5 artifacts — ``derived/edgar-<acq_id>/`` for the
identity map and the development event panel, ``reserve_sealed/edgar-<acq_id>/``
for reserve events — and a deterministic canonical ``manifest.json`` carrying
``ev_id``, the gate results and the counts.  It imports E-1/E-2/E-3 read-only,
never touches ``data_store`` (synthetic fixtures only), and makes no network
or provider call.
"""

from __future__ import annotations

import bisect
import math
import random
import statistics
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import pandas as pd

from smart_beta.dataset_v1.edgar_events import (
    EA_8K202,
    EventRow,
    default_session_calendar,
    load_raw_filings,
    normalize_key,
    sort_event_rows,
    split_by_firewall,
    write_event_panel,
)
from smart_beta.dataset_v1.edgar_fetch import canonical_json, sha256_hex
from smart_beta.dataset_v1.edgar_identity import (
    AUDIT_CONFIRMED,
    AUDIT_CONTRADICTED,
    AUDIT_NO_SYMBOL_EVIDENCE,
    AUDIT_NOT_SAMPLED,
    GATE_FAIL,
    GATE_NOT_CERTIFIED,
    GATE_PASS,
    IM_P_MAX_CONTRADICTED_RATE,
    IM_P_MAX_UPPER_BOUND,
    IM_P_MIN_EVIDENCE_ENDED,
    IM_P_MIN_EVIDENCE_TOTAL,
    ROUTE_NAME,
    ROUTE_TICKER,
    STATUS_FOREIGN_FILER,
    STATUS_MAPPED,
    IdentityRow,
    clopper_pearson_upper,
    load_edgar_reference,
    read_identity_map,
    resolve_identity,
    run_im_p,
    write_identity_map,
)
from smart_beta.dataset_v1.reserve_firewall import RESERVE_START, ReserveFirewallError
from smart_beta.dataset_v1.trusted_inputs import (
    INPUT_DAILY_TOTAL_RETURN,
    load_trusted_inputs,
)

__all__ = [
    "SCHEMA",
    "ACQ_DIR_PREFIX",
    "BUILD_DIR",
    "EVENTS_FILE",
    "IDENTITY_MAP_FILE",
    "MANIFEST_FILE",
    "DEVELOPMENT_START",
    "DEVELOPMENT_END",
    "EV_C_MEDIAN_MIN",
    "EV_C_FRACTION_MIN",
    "EV_T_SAMPLE_SIZE",
    "EV_T_THRESHOLD",
    "EV_T_TRAILING_SESSIONS",
    "DEFAULT_BUILD_SEED",
    "GATE_IM_P",
    "GATE_EV_C",
    "GATE_EV_T",
    "GATE_NAMES",
    "EdgarBuildError",
    "EdgarBuildInputError",
    "GateResult",
    "D7cDevelopmentReturns",
    "compute_ev_id",
    "read_identity_rows",
    "read_event_rows",
    "run_im_p_gate",
    "run_ev_c_gate",
    "run_ev_t_gate",
    "manifest_payload",
    "build",
]

SCHEMA = "discovery-dataset-v1-edgar-build-v1"

ACQ_DIR_PREFIX = "edgar-"
BUILD_DIR = "derived"
IDENTITY_MAP_FILE = "identity_map.parquet"
EVENTS_FILE = "events.parquet"
MANIFEST_FILE = "manifest.json"

#: Development window (V§1, plan §3): the D4 development calendar.
DEVELOPMENT_START = date(2015, 1, 2)
DEVELOPMENT_END = date(2023, 6, 30)

#: Gate thresholds (design freeze decision 3).
EV_C_MEDIAN_MIN = 3.0
EV_C_FRACTION_MIN = 0.80
EV_T_SAMPLE_SIZE = 200
EV_T_THRESHOLD = 0.60

#: EV-T trailing window, in sessions.  The frozen design fixes the statistic
#: (``|return(K(e))|`` vs the security's own trailing median ``|return|``) but
#: not the window length; one trading year (252 sessions) is the recorded
#: default, exposed as a parameter so the choice is explicit and reproducible.
EV_T_TRAILING_SESSIONS = 252

DEFAULT_BUILD_SEED = 1304

GATE_IM_P = "IM_P"
GATE_EV_C = "EV_C"
GATE_EV_T = "EV_T"
GATE_NAMES = (GATE_IM_P, GATE_EV_C, GATE_EV_T)


# ---------------------------------------------------------------------------
# Typed errors (fail closed)
# ---------------------------------------------------------------------------


class EdgarBuildError(Exception):
    """Base class for every E-4 fail-closed error."""


class EdgarBuildInputError(EdgarBuildError, ValueError):
    """An E-4 build argument or input is malformed."""


# ---------------------------------------------------------------------------
# Gate results
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GateResult:
    """One frozen gate's outcome and its reviewable metrics."""

    name: str
    status: str
    detail: str
    metrics: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        """A JSON-serializable, deterministically ordered gate dictionary."""
        return {
            "status": self.status,
            "detail": self.detail,
            "metrics": {key: self.metrics[key] for key in sorted(self.metrics)},
        }


def _as_date(value: Any) -> date | None:
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
# IM-P — aggregated from the E-2 stamped verdicts (V§3, plan §8)
# ---------------------------------------------------------------------------


def run_im_p_gate(rows: Iterable[IdentityRow]) -> GateResult:
    """Evaluate gate IM-P from the E-2 ``audit_verdict`` column.

    Only the sampled keys (``audit_verdict != NOT_SAMPLED``) enter the
    denominator; the binding §8 rule turns fewer than 120 evidence-bearing
    keys, or fewer than 50 ended evidence-bearing keys, into
    ``NOT_CERTIFIED`` rather than a weak ``PASS``.
    """
    rows = list(rows)
    sampled = [row for row in rows if row.audit_verdict != AUDIT_NOT_SAMPLED]
    n_confirmed = sum(1 for row in sampled if row.audit_verdict == AUDIT_CONFIRMED)
    n_contradicted = sum(
        1 for row in sampled if row.audit_verdict == AUDIT_CONTRADICTED
    )
    n_no_evidence = sum(
        1 for row in sampled if row.audit_verdict == AUDIT_NO_SYMBOL_EVIDENCE
    )
    n_evidence_total = n_confirmed + n_contradicted
    n_evidence_ended = sum(
        1
        for row in sampled
        if row.route == ROUTE_NAME
        and row.audit_verdict in (AUDIT_CONFIRMED, AUDIT_CONTRADICTED)
    )
    n_evidence_active = sum(
        1
        for row in sampled
        if row.route == ROUTE_TICKER
        and row.audit_verdict in (AUDIT_CONFIRMED, AUDIT_CONTRADICTED)
    )
    rate = (
        n_contradicted / n_evidence_total if n_evidence_total else None
    )
    upper = (
        float(clopper_pearson_upper(n_contradicted, n_evidence_total))
        if n_evidence_total
        else None
    )
    sufficient = (
        n_evidence_total >= int(IM_P_MIN_EVIDENCE_TOTAL)
        and n_evidence_ended >= int(IM_P_MIN_EVIDENCE_ENDED)
    )
    if not sufficient:
        status = GATE_NOT_CERTIFIED
        detail = "insufficient symbol evidence for IM-P"
    elif (
        rate is not None
        and rate <= IM_P_MAX_CONTRADICTED_RATE
        and upper is not None
        and upper <= IM_P_MAX_UPPER_BOUND
    ):
        status = GATE_PASS
        detail = "contradicted rate and one-sided 95% upper bound within bounds"
    else:
        status = GATE_FAIL
        detail = "contradicted rate or one-sided 95% upper bound too high"
    return GateResult(
        name=GATE_IM_P,
        status=status,
        detail=detail,
        metrics={
            "sample_size": len(sampled),
            "n_evidence_total": n_evidence_total,
            "n_evidence_active": n_evidence_active,
            "n_evidence_ended": n_evidence_ended,
            "n_confirmed": n_confirmed,
            "n_contradicted": n_contradicted,
            "n_no_symbol_evidence": n_no_evidence,
            "contradicted_rate": rate,
            "upper_bound": upper,
            "evidence_sufficient": sufficient,
        },
    )


# ---------------------------------------------------------------------------
# EV-C — MAPPED domestic EA_8K202 coverage (V§4)
# ---------------------------------------------------------------------------


def _listed_years(
    row: IdentityRow,
    development_start: date,
    development_end: date,
) -> list[int]:
    start = row.interval_start or development_start
    end = row.interval_end or development_end
    start = max(start, development_start)
    end = min(end, development_end)
    if start > end:
        return []
    return list(range(start.year, end.year + 1))


def run_ev_c_gate(
    events: Iterable[EventRow],
    identity_rows: Iterable[IdentityRow],
    *,
    development_start: date = DEVELOPMENT_START,
    development_end: date = DEVELOPMENT_END,
) -> GateResult:
    """Evaluate gate EV-C over MAPPED domestic keys and their listed years."""
    mapped = [row for row in identity_rows if row.status == STATUS_MAPPED]

    counts: Counter[tuple[str, int]] = Counter()
    for event in events:
        if event.event_type != EA_8K202:
            continue
        if not (development_start <= event.k_session <= development_end):
            continue
        counts[(event.security_key, event.k_session.year)] += 1

    key_years: list[int] = []
    for row in mapped:
        for year in _listed_years(row, development_start, development_end):
            key_years.append(int(counts.get((row.security_key, year), 0)))

    if not key_years:
        return GateResult(
            name=GATE_EV_C,
            status=GATE_NOT_CERTIFIED,
            detail="no mapped domestic key-years in development",
            metrics={
                "n_mapped_keys": len(mapped),
                "n_key_years": 0,
                "median_events": None,
                "fraction_at_least_3": None,
            },
        )

    median_events = float(statistics.median(key_years))
    fraction = sum(1 for value in key_years if value >= 3) / len(key_years)
    if median_events >= EV_C_MEDIAN_MIN and fraction >= EV_C_FRACTION_MIN:
        status = GATE_PASS
        detail = "median and coverage fraction meet the EV-C thresholds"
    else:
        status = GATE_FAIL
        detail = "median or coverage fraction below the EV-C thresholds"
    return GateResult(
        name=GATE_EV_C,
        status=status,
        detail=detail,
        metrics={
            "n_mapped_keys": len(mapped),
            "n_key_years": len(key_years),
            "median_events": median_events,
            "fraction_at_least_3": float(fraction),
        },
    )


# ---------------------------------------------------------------------------
# EV-T — knowledge-timing sanity check (V§4)
# ---------------------------------------------------------------------------


class D7cDevelopmentReturns:
    """A read-only, reserve-safe view over the D7c development daily returns.

    The wrapped ``values`` frame is the wide D7c ``daily_total_return``
    ``TrustedInput`` value frame (session index x ``security_key`` columns).
    Production construction is :meth:`from_root`, which reads the frozen D7c
    artifact through the existing
    :func:`~smart_beta.dataset_v1.trusted_inputs.load_trusted_inputs` accessor;
    tests may inject the same frame directly.

    Every lookup refuses a session at or after the sealed reserve boundary
    ``2023-07-03`` (V§5) and never substitutes another session, a fill, an
    interpolation or ``lambda = 1``.
    """

    def __init__(
        self,
        values: pd.DataFrame,
        *,
        input_name: str = INPUT_DAILY_TOTAL_RETURN,
    ) -> None:
        if not isinstance(values, pd.DataFrame):
            raise EdgarBuildInputError(
                "the D7c development returns frame must be a DataFrame"
            )
        self._values = values
        self._input_name = input_name
        self._positions: dict[date, Any] = {}
        for label in values.index:
            parsed = _as_date(label)
            if parsed is not None:
                self._positions[parsed] = label
        self._ordered = sorted(self._positions)

    @property
    def input_name(self) -> str:
        return self._input_name

    @classmethod
    def from_root(
        cls,
        root: str | Path,
        *,
        bindings: Any | None = None,
        calendar: Any | None = None,
    ) -> "D7cDevelopmentReturns":
        """Load the D7c ``daily_total_return`` ``TrustedInput`` (read-only)."""
        inputs = load_trusted_inputs(root, bindings=bindings, calendar=calendar)
        if INPUT_DAILY_TOTAL_RETURN not in inputs:
            raise EdgarBuildInputError(
                "the D7c artifact does not carry daily_total_return"
            )
        return cls(inputs[INPUT_DAILY_TOTAL_RETURN].values)

    def _assert_development(self, session: date) -> None:
        if session >= RESERVE_START:
            raise ReserveFirewallError(
                f"development returns may not read the sealed reserve session "
                f"{session} (boundary {RESERVE_START})"
            )

    def absolute_return(self, security_key: str, session: date) -> float | None:
        """``|daily_total_return|`` at ``session``, or ``None`` if unavailable."""
        self._assert_development(session)
        label = self._positions.get(session)
        if label is None or security_key not in self._values.columns:
            return None
        value = float(self._values.at[label, security_key])
        if not math.isfinite(value):
            return None
        return abs(value)

    def trailing_absolute_returns(
        self,
        security_key: str,
        session: date,
        sessions: int = EV_T_TRAILING_SESSIONS,
    ) -> list[float]:
        """The finite ``|daily_total_return|`` observations strictly before ``session``."""
        self._assert_development(session)
        if sessions <= 0:
            return []
        position = bisect.bisect_left(self._ordered, session)
        window = self._ordered[max(0, position - sessions) : position]
        out: list[float] = []
        for day in window:
            value = self.absolute_return(security_key, day)
            if value is not None:
                out.append(value)
        return out


def run_ev_t_gate(
    events: Iterable[EventRow],
    returns: D7cDevelopmentReturns,
    *,
    seed: int = DEFAULT_BUILD_SEED,
    sample_size: int = EV_T_SAMPLE_SIZE,
    trailing_sessions: int = EV_T_TRAILING_SESSIONS,
    threshold: float = EV_T_THRESHOLD,
) -> GateResult:
    """Evaluate gate EV-T on a seeded sample of development ``EA_8K202`` events."""
    candidates = [
        event
        for event in sort_event_rows(events)
        if event.event_type == EA_8K202 and event.k_session < RESERVE_START
    ]
    rng = random.Random(seed)
    take = min(int(sample_size), len(candidates))
    sample = rng.sample(candidates, take) if take else []

    usable = 0
    hits = 0
    missing = 0
    for event in sample:
        current = returns.absolute_return(event.security_key, event.k_session)
        trailing = returns.trailing_absolute_returns(
            event.security_key, event.k_session, trailing_sessions
        )
        if current is None or not trailing:
            missing += 1
            continue
        usable += 1
        if current > statistics.median(trailing):
            hits += 1

    fraction = (hits / usable) if usable else None
    sample_hash = sha256_hex(
        canonical_json([event.accession for event in sample])
    )
    if usable == 0 or usable < take:
        status = GATE_NOT_CERTIFIED
        detail = "insufficient usable development returns for the EV-T sample"
    elif fraction is not None and fraction >= threshold:
        status = GATE_PASS
        detail = "the knowledge session return exceeds the trailing median often enough"
    else:
        status = GATE_FAIL
        detail = "the knowledge session return rarely exceeds the trailing median"
    return GateResult(
        name=GATE_EV_T,
        status=status,
        detail=detail,
        metrics={
            "sample_size": len(sample),
            "target_sample_size": int(sample_size),
            "n_usable": usable,
            "n_missing": missing,
            "n_hits": hits,
            "fraction": float(fraction) if fraction is not None else None,
            "sample_sha256": sample_hash,
            "trailing_sessions": int(trailing_sessions),
            "seed": int(seed),
        },
    )


# ---------------------------------------------------------------------------
# Reading already-emitted E-2 / E-3 artifacts
# ---------------------------------------------------------------------------


def read_identity_rows(path: str | Path) -> list[IdentityRow]:
    """Read an E-2 ``identity_map.parquet`` into :class:`IdentityRow` values."""
    frame = read_identity_map(path)
    rows: list[IdentityRow] = []
    for record in frame.to_dict("records"):
        rows.append(
            IdentityRow(
                security_key=str(record["security_key"]),
                cik=None if pd.isna(record["cik"]) else int(record["cik"]),
                status=str(record["status"]),
                route=(
                    None
                    if record.get("route") is None or pd.isna(record.get("route"))
                    else str(record["route"])
                ),
                interval_start=_as_date(record.get("interval_start")),
                interval_end=_as_date(record.get("interval_end")),
                n_verified_candidates=int(record["n_verified_candidates"]),
                audit_verdict=str(record["audit_verdict"]),
            )
        )
    return rows


def read_event_rows(path: str | Path) -> list[EventRow]:
    """Read an E-3 ``events.parquet`` into :class:`EventRow` values."""
    frame = pd.read_parquet(path)
    rows: list[EventRow] = []
    for record in frame.to_dict("records"):
        rows.append(
            EventRow(
                security_key=str(record["security_key"]),
                cik=int(record["cik"]),
                accession=str(record["accession"]),
                form=str(record["form"]),
                event_type=str(record["event_type"]),
                acceptance_utc=datetime.fromisoformat(str(record["acceptance_utc"])),
                acceptance_et=datetime.fromisoformat(str(record["acceptance_et"])),
                k_session=_as_date(record["k_session"]),
                boundary_flag=bool(record["boundary_flag"]),
                report_period=_as_date(record.get("report_period")),
            )
        )
    return rows


# ---------------------------------------------------------------------------
# Deterministic manifest
# ---------------------------------------------------------------------------


def manifest_payload(
    acq_id: str,
    gates: Sequence[GateResult],
    counts: Mapping[str, Any],
) -> dict[str, Any]:
    """The canonical manifest body (everything except ``ev_id``)."""
    return {
        "schema": SCHEMA,
        "acq_id": str(acq_id),
        "gates": {gate.name: gate.as_dict() for gate in gates},
        "counts": {key: counts[key] for key in sorted(counts)},
    }


def compute_ev_id(payload: Mapping[str, Any]) -> str:
    """``ev_id`` = SHA-256 of the canonical manifest body."""
    return sha256_hex(canonical_json(payload))


def _manifest_bytes(manifest: Mapping[str, Any]) -> bytes:
    return (canonical_json(manifest) + "\n").encode("utf-8")


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------


def _build_dir(root: Path, acq_id: str) -> Path:
    return root / BUILD_DIR / f"{ACQ_DIR_PREFIX}{acq_id}"


def _raw_dir(root: Path, acq_id: str) -> Path:
    return root / "raw" / f"{ACQ_DIR_PREFIX}{acq_id}"


def _derive_identity_rows(
    raw_root: Path,
    keys: Iterable[Any],
    evidence: Mapping[str, Any] | Iterable[Any] | None,
    tickers: Mapping[str, Sequence[str]] | None,
    seed: int,
) -> list[IdentityRow]:
    reference = load_edgar_reference(raw_root)
    rows = resolve_identity(
        keys,
        company_tickers=reference.company_tickers,
        cik_lookup=reference.cik_lookup,
        submissions=reference.submissions,
        issuer_names=reference.issuer_names,
    )
    if evidence is not None or tickers is not None:
        rows, _ = run_im_p(
            rows,
            evidence=evidence or {},
            tickers=tickers or {},
            seed=seed,
        )
    return rows


def _derive_event_rows(
    raw_root: Path,
    identity_rows: Iterable[IdentityRow],
    calendar: Any,
) -> list[EventRow]:
    events: list[EventRow] = []
    for row in identity_rows:
        if row.status not in (STATUS_MAPPED, STATUS_FOREIGN_FILER):
            continue
        if row.cik is None:
            continue
        filings = load_raw_filings(raw_root, int(row.cik))
        events.extend(
            normalize_key(
                row.security_key,
                int(row.cik),
                filings,
                calendar=calendar,
            )
        )
    return sort_event_rows(events)


def build(
    root: str | Path,
    acq_id: str,
    *,
    identity_rows: Iterable[IdentityRow] | None = None,
    events: Iterable[EventRow] | None = None,
    returns: D7cDevelopmentReturns | None = None,
    keys: Iterable[Any] | None = None,
    evidence: Mapping[str, Any] | Iterable[Any] | None = None,
    tickers: Mapping[str, Sequence[str]] | None = None,
    calendar: Any | None = None,
    seed: int = DEFAULT_BUILD_SEED,
    sample_size: int = EV_T_SAMPLE_SIZE,
    trailing_sessions: int = EV_T_TRAILING_SESSIONS,
    development_start: date = DEVELOPMENT_START,
    development_end: date = DEVELOPMENT_END,
    write: bool = True,
) -> dict[str, Any]:
    """Evaluate the v1.3 gates and return the deterministic build manifest.

    The identity map and the event panel are taken from the explicit
    arguments when supplied, otherwise read from
    ``<root>/derived/edgar-<acq_id>/``, otherwise derived from the E-1 raw
    acquisition ``<root>/raw/edgar-<acq_id>/`` and the supplied D4 ``keys``.
    Development returns are read only through :class:`D7cDevelopmentReturns`
    (never a factor or long-short outcome).  Either way the returned manifest
    records the honest gate statuses, the counts and the ``ev_id``.
    """
    root = Path(root)
    build_directory = _build_dir(root, acq_id)
    raw_directory = _raw_dir(root, acq_id)

    if identity_rows is None:
        identity_path = build_directory / IDENTITY_MAP_FILE
        if identity_path.exists():
            identity_rows = read_identity_rows(identity_path)
        elif keys is not None:
            identity_rows = _derive_identity_rows(
                raw_directory, keys, evidence, tickers, seed
            )
        else:
            raise EdgarBuildInputError(
                "build needs identity_rows, an identity_map.parquet, or keys"
            )
    identity_rows = list(identity_rows)

    if events is None:
        events_path = build_directory / EVENTS_FILE
        if events_path.exists():
            events = read_event_rows(events_path)
        elif keys is not None:
            session_calendar = (
                calendar if calendar is not None else default_session_calendar()
            )
            events = _derive_event_rows(
                raw_directory, identity_rows, session_calendar
            )
        else:
            raise EdgarBuildInputError(
                "build needs events, an events.parquet, or keys"
            )
    events = sort_event_rows(list(events))

    if returns is None:
        returns = D7cDevelopmentReturns.from_root(root)
    if not isinstance(returns, D7cDevelopmentReturns):
        raise EdgarBuildInputError(
            "returns must be a D7cDevelopmentReturns accessor"
        )

    gates = [
        run_im_p_gate(identity_rows),
        run_ev_c_gate(
            events,
            identity_rows,
            development_start=development_start,
            development_end=development_end,
        ),
        run_ev_t_gate(
            events,
            returns,
            seed=seed,
            sample_size=sample_size,
            trailing_sessions=trailing_sessions,
        ),
    ]

    development_events, reserve_events = split_by_firewall(events)
    counts: dict[str, Any] = {
        "identity_rows": len(identity_rows),
        "mapped_keys": sum(
            1 for row in identity_rows if row.status == STATUS_MAPPED
        ),
        "foreign_filer_keys": sum(
            1 for row in identity_rows if row.status == STATUS_FOREIGN_FILER
        ),
        "unmapped_keys": sum(
            1
            for row in identity_rows
            if row.status
            not in (STATUS_MAPPED, STATUS_FOREIGN_FILER)
        ),
        "events": len(events),
        "ea_8k202_events": sum(
            1 for event in events if event.event_type == EA_8K202
        ),
        "development_events": len(development_events),
        "reserve_events": len(reserve_events),
        "event_types": sorted({event.event_type for event in events}),
    }

    payload = manifest_payload(acq_id, gates, counts)
    manifest = {**payload, "ev_id": compute_ev_id(payload)}

    if write:
        build_directory.mkdir(parents=True, exist_ok=True)
        write_identity_map(identity_rows, build_directory / IDENTITY_MAP_FILE)
        write_event_panel(
            root,
            acq_id,
            events,
            development_relpath=(
                f"{BUILD_DIR}/{ACQ_DIR_PREFIX}{acq_id}/{EVENTS_FILE}"
            ),
        )
        (build_directory / MANIFEST_FILE).write_bytes(_manifest_bytes(manifest))

    return manifest
