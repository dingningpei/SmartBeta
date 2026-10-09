"""E-2 — Dataset v1.3 identity map (IM) and IM-P precision audit.

Authority:

* the frozen design ``docs/discovery_dataset_v1_3_edgar_events_design.md``
  (``c9daa54b…b269``), "V§n" below;
* the frozen plan ``docs/discovery_dataset_v1_3_implementation_plan.md`` —
  §1 row E-2, §2 security, §3 interfaces, §4 routes/IM-P tests, §5
  mutations (prefix ``E2``), §7 stops, **and the binding §8 IM-P
  evidence-sufficiency rule (Q1)**.

The module maps every D4 ``security_key`` to a CIK (with a validity
interval) or to a typed non-mapping status, and audits the mapping
precision on a seeded sample of 200 MAPPED keys (100 active, 100 ended).

It is deliberately offline: it never opens a socket, never reads
``data_store/``, and reads only the bytes of an E-1 raw acquisition
directory (or synthetic fixtures).  All parsing is delegated to the frozen
E-1 helpers (``parse_company_tickers``, ``parse_cik_lookup``,
``iter_filings``, ``paginated_requests_from_submissions``,
``PERIODIC_FORMS``); E-1 is imported read-only.

Statuses (V§3)::

    MAPPED / AMBIGUOUS / NO_FILINGS_IN_INTERVAL / NO_CANDIDATE / FOREIGN_FILER

Routes::

    TICKER   — an active key resolved through ``company_tickers.json``;
    NAME     — an ended key resolved through ``cik-lookup-data.txt`` plus a
               ``formerNames`` interval overlap check;
    None     — the key carried neither a ticker nor an issuer name.

Every input key produces exactly one output row; unmapped keys are never
dropped (V§3, M2 limitation 2).  The IM-P audit is evaluated only on the
sampled keys **with** symbol evidence, and the §8 rule turns an undersized
evidence base into ``NOT_CERTIFIED`` rather than a weak ``PASS``.

As amended by V13-E3, the module also reads the stored E-1-R2 L2 primary
documents (``extract_trading_symbols``, ``build_symbol_evidence``) so the
audit's evidence is the independently observed cover-page
``dei:TradingSymbol``, normalized against the key's D4 ticker.
"""

from __future__ import annotations

import html
import json
import math
import random
import re
from dataclasses import dataclass, replace
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence

import pandas as pd
import pyarrow as pa

from smart_beta.dataset_v1.classification import norm as _norm
from smart_beta.dataset_v1.edgar_fetch import (
    PERIODIC_FORMS,
    iter_filings,
    paginated_requests_from_submissions,
    parse_cik_lookup,
    parse_company_tickers,
)

__all__ = [
    "SCHEMA",
    "IDENTITY_COLUMNS",
    "ROUTE_TICKER",
    "ROUTE_NAME",
    "ROUTES",
    "STATUS_MAPPED",
    "STATUS_AMBIGUOUS",
    "STATUS_NO_FILINGS_IN_INTERVAL",
    "STATUS_NO_CANDIDATE",
    "STATUS_FOREIGN_FILER",
    "STATUSES",
    "AUDIT_CONFIRMED",
    "AUDIT_CONTRADICTED",
    "AUDIT_NO_SYMBOL_EVIDENCE",
    "AUDIT_NOT_SAMPLED",
    "AUDIT_VERDICTS",
    "IM_P_SAMPLE_SIZE",
    "IM_P_PER_STRATUM",
    "IM_P_STRATUM_ACTIVE",
    "IM_P_STRATUM_ENDED",
    "IM_P_MIN_EVIDENCE_TOTAL",
    "IM_P_MIN_EVIDENCE_ENDED",
    "IM_P_MAX_CONTRADICTED_RATE",
    "IM_P_MAX_UPPER_BOUND",
    "IM_P_ALPHA",
    "GATE_PASS",
    "GATE_FAIL",
    "GATE_NOT_CERTIFIED",
    "DOMESTIC_PERIODIC_FORMS",
    "FOREIGN_PERIODIC_FORMS",
    "DEFAULT_IM_P_SEED",
    "EdgarIdentityError",
    "IdentityInputError",
    "IdentityKey",
    "SubmissionView",
    "EdgarReference",
    "IdentityRow",
    "SymbolEvidence",
    "ImPAuditResult",
    "normalize_symbol",
    "extract_trading_symbols",
    "build_symbol_evidence",
    "coerce_key",
    "load_edgar_reference",
    "parse_tiingo_names",
    "candidate_ciks",
    "resolve_identity",
    "identity_frame",
    "write_identity_map",
    "read_identity_map",
    "select_im_p_sample",
    "clopper_pearson_upper",
    "audit_im_p",
    "apply_audit_verdicts",
    "run_im_p",
]

SCHEMA = "discovery-dataset-v1-edgar-identity-v1"

#: The frozen E-2 output column order (plan §3).
IDENTITY_COLUMNS = (
    "security_key",
    "cik",
    "status",
    "route",
    "interval_start",
    "interval_end",
    "n_verified_candidates",
    "audit_verdict",
)

#: V§3 mapping routes.
ROUTE_TICKER = "TICKER"
ROUTE_NAME = "NAME"
ROUTES = (ROUTE_TICKER, ROUTE_NAME)

#: V§3 statuses.
STATUS_MAPPED = "MAPPED"
STATUS_AMBIGUOUS = "AMBIGUOUS"
STATUS_NO_FILINGS_IN_INTERVAL = "NO_FILINGS_IN_INTERVAL"
STATUS_NO_CANDIDATE = "NO_CANDIDATE"
STATUS_FOREIGN_FILER = "FOREIGN_FILER"
STATUSES = (
    STATUS_MAPPED,
    STATUS_AMBIGUOUS,
    STATUS_NO_FILINGS_IN_INTERVAL,
    STATUS_NO_CANDIDATE,
    STATUS_FOREIGN_FILER,
)

#: V§3 precision-audit verdicts.
AUDIT_CONFIRMED = "CONFIRMED"
AUDIT_CONTRADICTED = "CONTRADICTED"
AUDIT_NO_SYMBOL_EVIDENCE = "NO_SYMBOL_EVIDENCE"
AUDIT_NOT_SAMPLED = "NOT_SAMPLED"
AUDIT_VERDICTS = (
    AUDIT_CONFIRMED,
    AUDIT_CONTRADICTED,
    AUDIT_NO_SYMBOL_EVIDENCE,
    AUDIT_NOT_SAMPLED,
)

#: IM-P sampling and gate constants (V§3, plan §8).
IM_P_SAMPLE_SIZE = 200
IM_P_PER_STRATUM = 100
IM_P_STRATUM_ACTIVE = "ACTIVE"
IM_P_STRATUM_ENDED = "ENDED"
IM_P_MIN_EVIDENCE_TOTAL = 120
IM_P_MIN_EVIDENCE_ENDED = 50
IM_P_MAX_CONTRADICTED_RATE = 0.01
IM_P_MAX_UPPER_BOUND = 0.03
IM_P_ALPHA = 0.05  # one-sided 95%
DEFAULT_IM_P_SEED = 1302

GATE_PASS = "PASS"
GATE_FAIL = "FAIL"
GATE_NOT_CERTIFIED = "NOT_CERTIFIED"

DOMESTIC_PERIODIC_FORMS = frozenset({"10-K", "10-K/A", "10-Q", "10-Q/A"})
FOREIGN_PERIODIC_FORMS = frozenset({"20-F", "20-F/A", "40-F", "40-F/A"})

MAX_NAME_CANDIDATES = 5

_STRING = pd.ArrowDtype(pa.string())
_DATE = pd.ArrowDtype(pa.date32())
_INT = pd.ArrowDtype(pa.int64())


# ---------------------------------------------------------------------------
# Typed errors (fail closed)
# ---------------------------------------------------------------------------


class EdgarIdentityError(Exception):
    """Base class for every E-2 fail-closed error."""


class IdentityInputError(EdgarIdentityError, ValueError):
    """An identity input is malformed or an E-1 raw layout is incomplete."""


# ---------------------------------------------------------------------------
# Input records
# ---------------------------------------------------------------------------


def _as_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):  # includes pandas.Timestamp
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


@dataclass(frozen=True)
class IdentityKey:
    """One D4 key: its ticker, its listed interval and any issuer names."""

    security_key: str
    ticker: str | None = None
    interval_start: date | None = None
    interval_end: date | None = None
    issuer_names: tuple[str, ...] = ()


def _coerce_names(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        text = value.strip()
        return (text,) if text else ()
    if isinstance(value, Mapping):
        for key in ("issuer_names", "issuer_names_norm", "names", "name"):
            if key in value:
                return _coerce_names(value[key])
        return ()
    if isinstance(value, (list, tuple, set)):
        out: list[str] = []
        for item in value:
            if isinstance(item, str):
                if item.strip():
                    out.append(item)
            elif isinstance(item, Mapping):
                for key in ("issuer_name", "issuer_names", "name"):
                    if key in item:
                        out.extend(_coerce_names(item[key]))
                        break
            elif item is not None:
                text = str(item).strip()
                if text:
                    out.append(text)
        return tuple(out)
    return (str(value),)


def coerce_key(key: Any) -> IdentityKey:
    """Coerce a mapping or :class:`IdentityKey` into an :class:`IdentityKey`."""
    if isinstance(key, IdentityKey):
        return key
    if isinstance(key, Mapping):
        return IdentityKey(
            security_key=str(key["security_key"]),
            ticker=key.get("ticker"),
            interval_start=_as_date(key.get("interval_start") or key.get("listed_start")),
            interval_end=_as_date(key.get("interval_end") or key.get("listed_end")),
            issuer_names=_coerce_names(
                key.get("issuer_names") or key.get("issuer_names_norm") or key.get("names")
            ),
        )
    raise IdentityInputError(f"unsupported key record {key!r}")


@dataclass(frozen=True)
class SubmissionView:
    """The E-2 view of one CIK's submissions response.

    ``filings`` is the flattened list of filing rows (the ``recent`` arrays
    plus any paginated ``files/*.json`` rows).  ``former_names`` holds the
    ``(name, from, to)`` triples; a missing bound is ``None``.
    """

    cik: int
    entity_name: str | None = None
    former_names: tuple[tuple[str, date | None, date | None], ...] = ()
    filings: tuple[Mapping[str, Any], ...] = ()


def _former_names(payload: Mapping[str, Any]) -> tuple[tuple[str, date | None, date | None], ...]:
    raw = payload.get("formerNames")
    out: list[tuple[str, date | None, date | None]] = []
    if isinstance(raw, Mapping):
        raw = list(raw.values())
    if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
        for entry in raw:
            if not isinstance(entry, Mapping):
                continue
            name = entry.get("name")
            if not name:
                continue
            out.append(
                (
                    str(name),
                    _as_date(entry.get("from")),
                    _as_date(entry.get("to")),
                )
            )
    return tuple(out)


def _view_from_payload(
    payload: Mapping[str, Any],
    *,
    filings: Sequence[Mapping[str, Any]] | None = None,
) -> SubmissionView:
    if filings is None:
        filings = iter_filings(payload)
    return SubmissionView(
        cik=int(payload.get("cik") or 0),
        entity_name=payload.get("name"),
        former_names=_former_names(payload),
        filings=tuple(dict(row) for row in filings),
    )


@dataclass(frozen=True)
class EdgarReference:
    """The parsed E-1 reference files plus the per-CIK submission views."""

    company_tickers: Mapping[str, tuple[int, ...]]
    cik_lookup: Mapping[str, tuple[int, ...]]
    submissions: Mapping[int, SubmissionView]
    issuer_names: Mapping[str, tuple[str, ...]]


# ---------------------------------------------------------------------------
# Raw-layout loader (read-only, E-1 format)
# ---------------------------------------------------------------------------


def _json_loads(payload: bytes | str) -> Any:
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8")
    return json.loads(payload)


def parse_tiingo_names(payload: bytes | str | Any) -> dict[str, tuple[str, ...]]:
    """Parse ``tiingo_names.json`` into ``security_key -> (issuer names)``.

    The exact file shape is not frozen by E-1, so this accepts the natural
    spellings: ``{security_key: [names]}``, ``{security_key: {"name": ...}}``,
    ``{security_key: "name"}`` and the list-of-rows forms
    ``[{"security_key": ..., "issuer_names": [...]}]`` (also ``name`` /
    ``issuer_name``).  Only names with a non-empty string are kept.
    """
    if isinstance(payload, (bytes, str)):
        data = _json_loads(payload)
    else:
        data = payload

    def _names_from_rows(rows: Iterable[Any]) -> dict[str, tuple[str, ...]]:
        result: dict[str, tuple[str, ...]] = {}
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            security_key = row.get("security_key") or row.get("key")
            if security_key is None:
                continue
            names = _coerce_names(row)
            if names:
                result[str(security_key)] = names
        return result

    if isinstance(data, list):
        return _names_from_rows(data)
    if not isinstance(data, Mapping):
        return {}
    # A wrapper object holding a row list.
    for wrapper in ("records", "rows", "items", "data", "names"):
        inner = data.get(wrapper)
        if (
            isinstance(inner, list)
            and inner
            and isinstance(inner[0], Mapping)
            and ("security_key" in inner[0] or "key" in inner[0])
        ):
            return _names_from_rows(inner)
    result: dict[str, tuple[str, ...]] = {}
    for key, value in data.items():
        names = _coerce_names(value)
        if names:
            result[str(key)] = names
    return result


def _page_rows(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Flatten one paginated ``files/*.json`` page.

    The real SEC page carries the ``filings.recent`` parallel arrays at the
    top level; E-1's :func:`iter_filings` reads the ``recent`` shape and the
    ``{"filings": [...]}`` body.  Handle both here without changing E-1
    (imported read-only).
    """
    page_rows = iter_filings(payload)
    if page_rows:
        return page_rows
    if any(column in payload for column in ("accessionNumber", "form")):
        return iter_filings({"filings": {"recent": payload}})
    return page_rows


def load_edgar_reference(root: str | Path) -> EdgarReference:
    """Read an E-1 raw acquisition directory (read-only; no network)."""
    root = Path(root)
    company_path = root / "company_tickers.json"
    lookup_path = root / "cik-lookup-data.txt"
    if not company_path.exists():
        raise IdentityInputError(f"missing E-1 reference file {company_path}")
    if not lookup_path.exists():
        raise IdentityInputError(f"missing E-1 reference file {lookup_path}")

    company = parse_company_tickers(company_path.read_bytes())
    lookup = parse_cik_lookup(lookup_path.read_bytes())

    payloads: dict[int, Mapping[str, Any]] = {}
    submissions_dir = root / "submissions"
    if submissions_dir.is_dir():
        for path in sorted(submissions_dir.glob("CIK*.json")):
            payload = _json_loads(path.read_bytes())
            cik_value = payload.get("cik")
            if cik_value is None:
                stem = path.stem  # CIK0000320193
                cik_value = stem[3:]
            payloads[int(cik_value)] = payload

    paginated = paginated_requests_from_submissions(payloads)
    views: dict[int, SubmissionView] = {}
    for cik, payload in payloads.items():
        rows = list(iter_filings(payload))
        for name in paginated.get(int(cik), ()):
            page_path = submissions_dir / "files" / str(name)
            if page_path.exists():
                rows.extend(_page_rows(_json_loads(page_path.read_bytes())))
        views[int(cik)] = _view_from_payload(payload, filings=rows)

    names_path = root / "tiingo_names.json"
    issuer_names = (
        parse_tiingo_names(names_path.read_bytes()) if names_path.exists() else {}
    )
    return EdgarReference(
        company_tickers=company,
        cik_lookup=lookup,
        submissions=views,
        issuer_names=issuer_names,
    )


# ---------------------------------------------------------------------------
# Candidate routes
# ---------------------------------------------------------------------------


def _as_view(payload: Any) -> SubmissionView | None:
    if payload is None:
        return None
    if isinstance(payload, SubmissionView):
        return payload
    if isinstance(payload, Mapping):
        return _view_from_payload(payload)
    return None


def _intervals_overlap(
    a_start: date | None,
    a_end: date | None,
    b_start: date | None,
    b_end: date | None,
) -> bool:
    """Inclusive overlap; a missing bound is open-ended."""
    if a_start is not None and b_end is not None and a_start > b_end:
        return False
    if b_start is not None and a_end is not None and b_start > a_end:
        return False
    return True


def _latest_former_end(view: SubmissionView) -> date | None:
    ends = [end for _name, _start, end in view.former_names if end is not None]
    return max(ends) if ends else None


def _name_matches_interval(
    names: Sequence[str],
    interval_start: date | None,
    interval_end: date | None,
    view: SubmissionView,
) -> bool:
    """True iff a name matches with an overlap of the key's listed interval.

    The current ``name`` is treated as open-ended from the latest
    ``formerNames`` end; each former name carries its own ``[from, to]``.
    """
    targets = {_norm(name) for name in names if _norm(name)}
    if not targets:
        return False
    if view.entity_name and _norm(view.entity_name) in targets:
        if _intervals_overlap(
            _latest_former_end(view), None, interval_start, interval_end
        ):
            return True
    for name, start, end in view.former_names:
        if _norm(name) in targets and _intervals_overlap(
            start, end, interval_start, interval_end
        ):
            return True
    return False


def _ticker_candidates(
    key: IdentityKey, company_tickers: Mapping[str, Sequence[int]]
) -> tuple[int, ...]:
    if not key.ticker:
        return ()
    return tuple(sorted(int(cik) for cik in company_tickers.get(str(key.ticker).upper(), ())))


def _name_candidates(
    key: IdentityKey,
    issuer_names: Mapping[str, Sequence[str]],
    cik_lookup: Mapping[str, Sequence[int]],
    submissions: Mapping[int, SubmissionView],
    *,
    max_name_candidates: int = MAX_NAME_CANDIDATES,
) -> tuple[int, ...]:
    names: list[str] = list(key.issuer_names)
    names.extend(str(name) for name in issuer_names.get(key.security_key, ()))
    found: set[int] = set()
    for name in names:
        normalized = _norm(name)
        if not normalized:
            continue
        hits = sorted(int(cik) for cik in cik_lookup.get(normalized, ()))
        found.update(hits[:max_name_candidates])
    restricted: list[int] = []
    for cik in sorted(found):
        view = submissions.get(int(cik))
        if view is None:
            # Fail closed: an unfetched candidate has no interval evidence.
            continue
        if _name_matches_interval(names, key.interval_start, key.interval_end, view):
            restricted.append(int(cik))
    return tuple(sorted(restricted))


def candidate_ciks(
    key: IdentityKey | Mapping[str, Any],
    *,
    company_tickers: Mapping[str, Sequence[int]],
    cik_lookup: Mapping[str, Sequence[int]],
    submissions: Mapping[int, Any] | None = None,
    issuer_names: Mapping[str, Sequence[str]] | None = None,
    max_name_candidates: int = MAX_NAME_CANDIDATES,
) -> tuple[tuple[int, ...], str | None]:
    """The candidate CIKs and the route for one key.

    The ticker route is attempted only when the key's ticker is a current
    ``company_tickers.json`` entry; otherwise the name route is attempted.
    """
    key = coerce_key(key)
    submissions = submissions or {}
    views = {int(cik): _as_view(payload) for cik, payload in submissions.items()}
    views = {cik: view for cik, view in views.items() if view is not None}
    if key.ticker and str(key.ticker).upper() in company_tickers:
        return _ticker_candidates(key, company_tickers), ROUTE_TICKER
    if key.issuer_names or (issuer_names and issuer_names.get(key.security_key)):
        return (
            _name_candidates(
                key,
                issuer_names or {},
                cik_lookup,
                views,
                max_name_candidates=max_name_candidates,
            ),
            ROUTE_NAME,
        )
    return (), None


# ---------------------------------------------------------------------------
# Verification and status assignment
# ---------------------------------------------------------------------------


def _periodic_flags(
    view: SubmissionView,
    interval_start: date | None,
    interval_end: date | None,
) -> tuple[bool, bool]:
    """``(has_domestic, has_foreign)`` periodic filings inside the interval."""
    has_domestic = False
    has_foreign = False
    for row in view.filings:
        form = str(row.get("form") or "")
        if form not in PERIODIC_FORMS:
            continue
        filed = _as_date(row.get("filingDate") or row.get("filing_date"))
        if filed is None:
            continue
        if interval_start is not None and filed < interval_start:
            continue
        if interval_end is not None and filed > interval_end:
            continue
        if form in DOMESTIC_PERIODIC_FORMS:
            has_domestic = True
        elif form in FOREIGN_PERIODIC_FORMS:
            has_foreign = True
    return has_domestic, has_foreign


@dataclass(frozen=True)
class IdentityRow:
    """One row of the frozen E-2 identity map (plan §3)."""

    security_key: str
    cik: int | None
    status: str
    route: str | None
    interval_start: date | None
    interval_end: date | None
    n_verified_candidates: int
    audit_verdict: str = AUDIT_NOT_SAMPLED


def _resolve_one(
    key: IdentityKey,
    *,
    company_tickers: Mapping[str, Sequence[int]],
    cik_lookup: Mapping[str, Sequence[int]],
    submissions: Mapping[int, SubmissionView],
    issuer_names: Mapping[str, Sequence[str]],
    max_name_candidates: int,
) -> IdentityRow:
    candidates, route = candidate_ciks(
        key,
        company_tickers=company_tickers,
        cik_lookup=cik_lookup,
        submissions=submissions,
        issuer_names=issuer_names,
        max_name_candidates=max_name_candidates,
    )

    verified: list[int] = []
    foreign_only = True
    for cik in candidates:
        view = submissions.get(int(cik))
        if view is None:
            continue
        has_domestic, has_foreign = _periodic_flags(
            view, key.interval_start, key.interval_end
        )
        if has_domestic or has_foreign:
            verified.append(int(cik))
        if has_domestic:
            foreign_only = False

    if not verified:
        status = (
            STATUS_NO_CANDIDATE if not candidates else STATUS_NO_FILINGS_IN_INTERVAL
        )
        cik_value: int | None = None
    elif len(verified) > 1:
        status = STATUS_AMBIGUOUS
        cik_value = None
    elif foreign_only:
        status = STATUS_FOREIGN_FILER
        cik_value = verified[0]
    else:
        status = STATUS_MAPPED
        cik_value = verified[0]

    return IdentityRow(
        security_key=key.security_key,
        cik=cik_value,
        status=status,
        route=route,
        interval_start=key.interval_start,
        interval_end=key.interval_end,
        n_verified_candidates=len(verified),
        audit_verdict=AUDIT_NOT_SAMPLED,
    )


def resolve_identity(
    keys: Iterable[Any],
    *,
    company_tickers: Mapping[str, Sequence[int]],
    cik_lookup: Mapping[str, Sequence[int]],
    submissions: Mapping[int, Any],
    issuer_names: Mapping[str, Sequence[str]] | None = None,
    max_name_candidates: int = MAX_NAME_CANDIDATES,
) -> list[IdentityRow]:
    """Resolve every key to a CIK or a typed non-mapping status.

    One :class:`IdentityRow` is emitted per input key, in the input order;
    unmapped keys are retained (V§3, M2 limitation 2).
    """
    issuer_names = issuer_names or {}
    views: dict[int, SubmissionView] = {}
    for cik, payload in submissions.items():
        view = _as_view(payload)
        if view is not None:
            views[int(cik)] = view
    rows: list[IdentityRow] = []
    for raw in keys:
        key = coerce_key(raw)
        rows.append(
            _resolve_one(
                key,
                company_tickers=company_tickers,
                cik_lookup=cik_lookup,
                submissions=views,
                issuer_names=issuer_names,
                max_name_candidates=max_name_candidates,
            )
        )
    return rows


# ---------------------------------------------------------------------------
# Output frame (frozen schema)
# ---------------------------------------------------------------------------


def _rows_sorted(rows: Iterable[IdentityRow]) -> list[IdentityRow]:
    return sorted(rows, key=lambda row: row.security_key)


def identity_frame(rows: Iterable[IdentityRow]) -> pd.DataFrame:
    """The deterministic ``identity_map.parquet`` frame (plan §3)."""
    ordered = _rows_sorted(rows)
    return pd.DataFrame(
        {
            "security_key": pd.array(
                [row.security_key for row in ordered], dtype=_STRING
            ),
            "cik": pd.array([row.cik for row in ordered], dtype=_INT),
            "status": pd.array([row.status for row in ordered], dtype=_STRING),
            "route": pd.array([row.route for row in ordered], dtype=_STRING),
            "interval_start": pd.array(
                [row.interval_start for row in ordered], dtype=_DATE
            ),
            "interval_end": pd.array(
                [row.interval_end for row in ordered], dtype=_DATE
            ),
            "n_verified_candidates": pd.array(
                [row.n_verified_candidates for row in ordered], dtype=_INT
            ),
            "audit_verdict": pd.array(
                [row.audit_verdict for row in ordered], dtype=_STRING
            ),
        },
        columns=list(IDENTITY_COLUMNS),
    )


def write_identity_map(rows: Iterable[IdentityRow], path: str | Path) -> Path:
    """Write the identity map parquet (deterministic row order)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    identity_frame(rows).to_parquet(
        path, engine="pyarrow", index=False, compression="zstd"
    )
    return path


def read_identity_map(path: str | Path) -> pd.DataFrame:
    return pd.read_parquet(path)


# ---------------------------------------------------------------------------
# IM-P — seeded sample, L2 symbol evidence (V13-E3 §2), audit, gate
# ---------------------------------------------------------------------------

#: The E-1-R2 raw-store directory holding the L2 primary documents.
SYMBOL_EVIDENCE_DIR = "symbol_evidence"

#: The E-1-R2 record of keys with no inline-XBRL periodic filing.
NO_INLINE_XBRL_FILE = "no_inline_xbrl.json"

#: An inline-XBRL fact whose ``name`` is ``dei:TradingSymbol`` (any case).
_TRADING_SYMBOL_RE = re.compile(
    r"<ix:nonNumeric\b[^>]*\bname\s*=\s*[\"']dei:TradingSymbol[\"'][^>]*>"
    r"(.*?)</ix:nonNumeric>",
    re.IGNORECASE | re.DOTALL,
)
_TAG_RE = re.compile(r"<[^>]+>")


def normalize_symbol(value: Any) -> str:
    """Uppercase ``value`` and drop ``.``, ``-``, ``/`` and whitespace.

    ``BRK.B``, ``BRK-B``, ``brk b`` and ``BRK/B`` all normalize to ``BRKB``
    so a D4 ticker and its inline-XBRL spelling compare equal.
    """
    text = str(value).upper()
    for ch in (".", "-", "/"):
        text = text.replace(ch, "")
    return "".join(text.split())


def extract_trading_symbols(document_bytes: bytes | str) -> tuple[str, ...]:
    """Every normalized ``dei:TradingSymbol`` in an inline-XBRL document.

    Each inline-XBRL fact inside ``ix:nonNumeric`` whose ``name`` is
    ``dei:TradingSymbol`` (matched case-insensitively) is stripped of its
    inner tags and whitespace, uppercased and normalized with
    :func:`normalize_symbol`; empty values are dropped.  The result is a
    sorted, distinct tuple.  A malformed or absent tag yields no symbol.
    """
    if isinstance(document_bytes, bytes):
        text = document_bytes.decode("utf-8", errors="replace")
    else:
        text = str(document_bytes)
    symbols: set[str] = set()
    for inner in _TRADING_SYMBOL_RE.findall(text):
        cleaned = html.unescape(_TAG_RE.sub(" ", inner))
        normalized = normalize_symbol(cleaned)
        if normalized:
            symbols.add(normalized)
    return tuple(sorted(symbols))


def _read_no_inline_keys(root: Path) -> frozenset[str]:
    """The E-1-R2 ``no_inline_xbrl.json`` security keys (sorted list)."""
    path = root / SYMBOL_EVIDENCE_DIR / NO_INLINE_XBRL_FILE
    if not path.exists():
        return frozenset()
    data = _json_loads(path.read_bytes())
    if not isinstance(data, list):
        return frozenset()
    return frozenset(str(item) for item in data if item is not None)


def _row_field(row: Any, name: str) -> Any:
    """Read ``name`` from a mapping row or an attribute row (E-1 request)."""
    if isinstance(row, Mapping):
        return row.get(name)
    return getattr(row, name, None)


def _row_document_paths(root: Path, row: Any) -> Iterator[Path]:
    """The stored primary-document paths for one L2 row.

    The row may carry an explicit ``relpath`` (E-1's ``FetchRequest``), the
    ``cik`` plus ``accession``/``primaryDocument`` triple (from which the
    E-1-R2 relpath is rebuilt), or the ``cik`` alone (every stored document
    under that CIK is read).
    """
    relpath = _row_field(row, "relpath") or _row_field(row, "path")
    if relpath:
        yield root / str(relpath)
        return
    cik = _row_field(row, "cik")
    if cik is None:
        return
    accession = _row_field(row, "accession") or _row_field(row, "accessionNumber")
    document = _row_field(row, "primaryDocument") or _row_field(
        row, "primary_document"
    )
    if accession and document:
        folder = str(accession).replace("-", "")
        yield root / SYMBOL_EVIDENCE_DIR / str(int(cik)) / folder / str(document)
        return
    base = root / SYMBOL_EVIDENCE_DIR / str(int(cik))
    if base.is_dir():
        for accession_dir in sorted(base.iterdir()):
            if not accession_dir.is_dir():
                continue
            for doc in sorted(accession_dir.iterdir()):
                if doc.is_file() and doc.suffix.lower() != ".json":
                    yield doc


def _symbol_verdict(
    observed_symbols: Sequence[str], ticker: str | None
) -> str:
    """The E-2-R2 verdict for one key's extracted symbol union.

    CONFIRMED when the normalized D4 ticker is one of the observed symbols,
    CONTRADICTED when the union is non-empty but the ticker is absent, and
    NO_SYMBOL_EVIDENCE when there is no union (including a key recorded as
    ``NO_INLINE_XBRL_FILING``).
    """
    if not observed_symbols:
        return AUDIT_NO_SYMBOL_EVIDENCE
    normalized_ticker = normalize_symbol(ticker) if ticker else ""
    if normalized_ticker and normalized_ticker in set(observed_symbols):
        return AUDIT_CONFIRMED
    return AUDIT_CONTRADICTED


def build_symbol_evidence(
    raw_root: str | Path,
    sample_rows: Iterable[Any],
) -> dict[str, SymbolEvidence]:
    """Read the stored L2 documents and classify each sampled key.

    ``raw_root`` is an E-1 raw acquisition directory.  Each row of
    ``sample_rows`` names a sampled key (``security_key``, ``cik`` and, for
    the verdict, ``ticker``) and either an explicit document ``relpath``, the
    ``accession``/``primaryDocument`` pair, or just its ``cik``.  Keys listed
    in ``symbol_evidence/no_inline_xbrl.json`` carry no documents and are
    always NO_SYMBOL_EVIDENCE.  The observed symbols are the sorted union of
    :func:`extract_trading_symbols` over the key's fetched documents, and the
    verdict follows :func:`_symbol_verdict`.  IM-P's :func:`audit_im_p`
    recomputes the gate verdicts from ``observed_symbols`` unchanged.
    """
    root = Path(raw_root)
    no_inline = _read_no_inline_keys(root)
    observed: dict[str, set[str]] = {}
    tickers: dict[str, str] = {}
    order: list[str] = []
    for row in sample_rows:
        key = _row_field(row, "security_key")
        if key is None:
            continue
        key = str(key)
        if key not in observed:
            observed[key] = set()
            order.append(key)
        ticker = _row_field(row, "ticker")
        if ticker:
            tickers.setdefault(key, str(ticker))
        if key in no_inline:
            continue
        for path in _row_document_paths(root, row):
            if path.is_file():
                observed[key].update(extract_trading_symbols(path.read_bytes()))
    result: dict[str, SymbolEvidence] = {}
    for key in order:
        symbols = tuple(sorted(observed[key]))
        result[key] = SymbolEvidence(
            key, symbols, _symbol_verdict(symbols, tickers.get(key))
        )
    for key in sorted(no_inline):
        result.setdefault(key, SymbolEvidence(key, (), AUDIT_NO_SYMBOL_EVIDENCE))
    return result


@dataclass(frozen=True)
class SymbolEvidence:
    """The independently observed cover-page trading symbol(s) for a key.

    ``verdict`` is the E-2-R2 classification of those symbols against the
    key's normalized D4 ticker (``CONFIRMED`` / ``CONTRADICTED`` /
    ``NO_SYMBOL_EVIDENCE``); it is informational, and :func:`audit_im_p`
    stays the gate's verdict authority, recomputing from ``observed_symbols``.
    """

    security_key: str
    observed_symbols: tuple[str, ...] = ()
    verdict: str = AUDIT_NOT_SAMPLED


def _coerce_evidence(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, SymbolEvidence):
        return tuple(value.observed_symbols)
    if isinstance(value, str):
        return (value,) if value.strip() else ()
    if isinstance(value, Mapping):
        for key in ("observed_symbols", "symbols", "symbol", "trading_symbol"):
            if key in value:
                return _coerce_evidence(value[key])
        return ()
    if isinstance(value, (list, tuple, set)):
        out: list[str] = []
        for item in value:
            out.extend(_coerce_evidence(item))
        return tuple(out)
    return (str(value),)


def _evidence_map(
    evidence: Mapping[str, Any] | Iterable[Any] | None,
) -> dict[str, tuple[str, ...]]:
    result: dict[str, tuple[str, ...]] = {}
    if evidence is None:
        return result
    if isinstance(evidence, Mapping):
        for key, value in evidence.items():
            result[str(key)] = _coerce_evidence(value)
        return result
    for item in evidence:
        if isinstance(item, SymbolEvidence):
            result[item.security_key] = tuple(item.observed_symbols)
        elif isinstance(item, Mapping):
            key = item.get("security_key")
            if key is None:
                continue
            result[str(key)] = _coerce_evidence(item)
    return result


def _stratum(row: IdentityRow) -> str | None:
    if row.route == ROUTE_TICKER:
        return IM_P_STRATUM_ACTIVE
    if row.route == ROUTE_NAME:
        return IM_P_STRATUM_ENDED
    return None


def select_im_p_sample(
    rows: Iterable[IdentityRow],
    *,
    seed: int = DEFAULT_IM_P_SEED,
    per_stratum: int = IM_P_PER_STRATUM,
) -> tuple[str, ...]:
    """The seeded 100-active / 100-ended sample of MAPPED keys (V§3).

    The candidate lists are sorted before sampling, so the selection is
    invariant to the input row order and reproducible from ``seed``.
    """
    mapped = [row for row in rows if row.status == STATUS_MAPPED]
    active = sorted(
        row.security_key for row in mapped if _stratum(row) == IM_P_STRATUM_ACTIVE
    )
    ended = sorted(
        row.security_key for row in mapped if _stratum(row) == IM_P_STRATUM_ENDED
    )
    rng = random.Random(seed)
    chosen_active = rng.sample(active, min(per_stratum, len(active)))
    chosen_ended = rng.sample(ended, min(per_stratum, len(ended)))
    return tuple(sorted(str(key) for key in chosen_active + chosen_ended))


def clopper_pearson_upper(k: int, n: int, alpha: float = IM_P_ALPHA) -> float:
    """The exact one-sided ``1 - alpha`` Clopper-Pearson upper bound.

    ``UB = Beta.ppf(1 - alpha, k + 1, n - k)``; it is the exact "exact"
    binomial interval, not the normal approximation.
    """
    n = int(n)
    k = max(0, min(int(k), n))
    if n <= 0:
        return 1.0
    if k >= n:
        return 1.0
    try:
        from scipy.stats import beta as _beta

        return float(_beta.ppf(1.0 - alpha, k + 1, n - k))
    except Exception:  # pragma: no cover - scipy is in the venv
        return _cp_upper_bisect(k, n, alpha)


def _binom_cdf(k: int, n: int, p: float) -> float:
    """``P(X <= k)`` for ``X ~ Binomial(n, p)`` (bisection fallback only)."""
    if p <= 0.0:
        return 1.0
    if p >= 1.0:
        return 0.0 if k < n else 1.0
    total = 0.0
    for i in range(0, k + 1):
        total += (
            math.comb(n, i) * (p**i) * ((1.0 - p) ** (n - i))
        )
    return total


def _cp_upper_bisect(k: int, n: int, alpha: float) -> float:
    lo, hi = 0.0, 1.0
    for _ in range(200):
        mid = (lo + hi) / 2.0
        # P(X <= k) is decreasing in p.
        if _binom_cdf(k, n, mid) > alpha:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2.0


@dataclass(frozen=True)
class ImPAuditResult:
    """The IM-P audit outcome (V§3 gate plus the binding §8 rule)."""

    sample_size: int
    sample_active: int
    sample_ended: int
    n_evidence_total: int
    n_evidence_active: int
    n_evidence_ended: int
    n_confirmed: int
    n_contradicted: int
    n_no_symbol_evidence: int
    contradicted_rate: float | None
    upper_bound: float | None
    evidence_sufficient: bool
    gate_status: str
    ended_stratum_certified: bool
    verdicts: Mapping[str, str]


def audit_im_p(
    rows: Iterable[IdentityRow],
    sample: Sequence[str],
    evidence: Mapping[str, Any] | Iterable[Any] | None,
    tickers: Mapping[str, Sequence[str]] | None = None,
    *,
    seed: int | None = None,
    min_evidence_total: int = IM_P_MIN_EVIDENCE_TOTAL,
    min_evidence_ended: int = IM_P_MIN_EVIDENCE_ENDED,
    max_contradicted_rate: float = IM_P_MAX_CONTRADICTED_RATE,
    max_upper_bound: float = IM_P_MAX_UPPER_BOUND,
    alpha: float = IM_P_ALPHA,
) -> ImPAuditResult:
    """Audit the sampled keys' symbol evidence and evaluate gate IM-P.

    A key is CONFIRMED when one observed symbol equals an accepted
    (ticker-history aware) D4 ticker, CONTRADICTED when evidence exists but
    none matches, and NO_SYMBOL_EVIDENCE when none was observed.  The gate
    is evaluated **only** on the keys with symbol evidence; the §8 rule
    downgrades an undersized evidence base to ``NOT_CERTIFIED``.
    """
    tickers = tickers or {}
    evidence_map = _evidence_map(evidence)
    by_key = {row.security_key: row for row in rows}
    sample = [str(key) for key in sample]

    verdicts: dict[str, str] = {}
    n_confirmed = 0
    n_contradicted = 0
    n_no_evidence = 0
    evidence_active = 0
    evidence_ended = 0
    sample_active = 0
    sample_ended = 0
    for key in sample:
        row = by_key.get(key)
        stratum = _stratum(row) if row is not None else None
        if stratum == IM_P_STRATUM_ACTIVE:
            sample_active += 1
        elif stratum == IM_P_STRATUM_ENDED:
            sample_ended += 1
        observed = tuple(str(s).strip().upper() for s in evidence_map.get(key, ()))
        observed = tuple(s for s in observed if s)
        if not observed:
            verdicts[key] = AUDIT_NO_SYMBOL_EVIDENCE
            n_no_evidence += 1
            continue
        accepted = {
            str(t).strip().upper()
            for t in (tickers.get(key, ()) if tickers else ())
            if str(t).strip()
        }
        if accepted and any(symbol in accepted for symbol in observed):
            verdicts[key] = AUDIT_CONFIRMED
            n_confirmed += 1
        else:
            verdicts[key] = AUDIT_CONTRADICTED
            n_contradicted += 1
        if stratum == IM_P_STRATUM_ACTIVE:
            evidence_active += 1
        elif stratum == IM_P_STRATUM_ENDED:
            evidence_ended += 1

    n_evidence_total = n_confirmed + n_contradicted
    rate = (n_contradicted / n_evidence_total) if n_evidence_total else None
    upper = (
        clopper_pearson_upper(n_contradicted, n_evidence_total, alpha)
        if n_evidence_total
        else None
    )
    sufficient = (
        n_evidence_total >= int(min_evidence_total)
        and evidence_ended >= int(min_evidence_ended)
    )
    if not sufficient:
        gate = GATE_NOT_CERTIFIED
    elif (
        rate is not None
        and rate <= max_contradicted_rate
        and upper is not None
        and upper <= max_upper_bound
    ):
        gate = GATE_PASS
    else:
        gate = GATE_FAIL

    return ImPAuditResult(
        sample_size=len(sample),
        sample_active=sample_active,
        sample_ended=sample_ended,
        n_evidence_total=n_evidence_total,
        n_evidence_active=evidence_active,
        n_evidence_ended=evidence_ended,
        n_confirmed=n_confirmed,
        n_contradicted=n_contradicted,
        n_no_symbol_evidence=n_no_evidence,
        contradicted_rate=rate,
        upper_bound=upper,
        evidence_sufficient=sufficient,
        gate_status=gate,
        ended_stratum_certified=sufficient,
        verdicts=verdicts,
    )


def apply_audit_verdicts(
    rows: Iterable[IdentityRow], verdicts: Mapping[str, str]
) -> list[IdentityRow]:
    """Return the rows with the sampled ``audit_verdict`` columns filled in."""
    return [
        replace(row, audit_verdict=verdicts.get(row.security_key, row.audit_verdict))
        if row.security_key in verdicts
        else row
        for row in rows
    ]


def run_im_p(
    rows: Iterable[IdentityRow],
    *,
    evidence: Mapping[str, Any] | Iterable[Any] | None,
    tickers: Mapping[str, Sequence[str]] | None = None,
    seed: int = DEFAULT_IM_P_SEED,
    **kwargs: Any,
) -> tuple[list[IdentityRow], ImPAuditResult]:
    """Select the sample, audit it, and stamp the sampled ``audit_verdict``s."""
    rows = list(rows)
    sample = select_im_p_sample(rows, seed=seed)
    result = audit_im_p(rows, sample, evidence, tickers, seed=seed, **kwargs)
    return apply_audit_verdicts(rows, result.verdicts), result
