"""Discovery Dataset v1.1 — D-SC security classification artifact.

D-SC sits between the D4 identity resolver and D6.  For every D4
``INTERVAL_ASSIGNED`` key it derives, as a point-in-time timeline:

* a **security class** (``COMMON_STOCK`` / ``NON_COMMON`` /
  ``CLASS_UNRESOLVED`` / ``EVIDENCE_CONFLICT``) from the archived Nasdaq
  Trader directory captures, the authoritative test-symbol list and the
  ticker-suffix rules; and
* an **issuer / policy exclusion overlay** (``EXCLUDED`` /
  ``ISSUER_STATUS_UNRESOLVED`` / ``NO_REGULATORY_EXCLUSION_OBSERVED``) from
  the SEC full-index regulatory events and the directory-name heuristics.

Governing authority (frozen): ``worker_tasks/dataset_v1/task-dsc-classifier.md``
and its normative Appendix A, the frozen vocabulary
``worker_tasks/dataset_v1/dsc-vocabulary-v4.md`` (and the v3/v2/v1
vocabularies it incorporates by reference), bound by
``docs/discovery_dataset_v1_1_classification_amendment.md``.  Semantics are
frozen; this module restructures Appendix A but does not change it.

The module is deliberately offline: it reads one D4 identity run, the
archived directory captures and the SEC full-index only.  It never makes a
provider or network call, never touches a credential and never reads a price,
volume, return or performance field.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import shutil
import tempfile
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import pandas as pd
import pyarrow as pa

__all__ = [
    "SCHEMA",
    "C",
    "AMENDMENT_SHA256",
    "VOCABULARY_SHA256",
    "TESTSYM",
    "ClassificationError",
    "ClassificationInputError",
    "ClassificationBindingError",
    "ClassificationNondeterministicError",
    "classify",
    "classify_string",
    "string_class",
    "class_state_at",
    "policy_state_at",
    "load_class_timeline",
    "load_policy_timeline",
    "state_at",
    "v3_points",
    "sec_overlay",
    "name_evidence",
    "policy",
    "issuer",
    "norm",
    "suffix_reason",
    "canonical_json",
]

SCHEMA = "discovery-dataset-v1-classification-v1"

# The frozen reference cutoff (task spec §3.4 / amendment §6).
C = date(2026, 8, 31)

# The frozen document hashes (task spec Authority and amendment §2).  A file
# whose bytes do not hash to these values is refused.
AMENDMENT_SHA256 = "f89a7128bb8113c6978fc6bca8bcaf6048cd0c7db1fe5ac2aaffa1c7d519c623"
VOCABULARY_SHA256 = "fd7484328c1c1efae2c02f334571f8bcea7d363c051bcccf49594d2dab34f84d"

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_AMENDMENT_PATH = (
    _REPO_ROOT / "docs" / "discovery_dataset_v1_1_classification_amendment.md"
)
_DEFAULT_VOCABULARY_PATH = (
    _REPO_ROOT / "worker_tasks" / "dataset_v1" / "dsc-vocabulary-v4.md"
)

TESTSYM = frozenset(
    {"NTEST", "ATEST-G", "ATEST-H", "ATEST-L", "PTEST-W", "ZTEST", "ZVZZT"}
)

_CAPTURE_RE = re.compile(r"^(nasdaqlisted|otherlisted|nasdaqtraded)_(\d{14})\.txt$")
_MASTER_RE = re.compile(r"^master_(\d{4})Q([1-4])\.gz$")

_REGULATORY_FORMS = frozenset(
    {
        "N-54A",
        "N-54A/A",
        "N-54C",
        "N-54C/A",
        "N-8A",
        "N-8A/A",
        "N-8F",
        "N-8F/A",
    }
)

# Instrument-level NON_COMMON reasons in v4 order (§1.4 of the vocabulary).
_NON_INSTRUMENT: tuple[tuple[str, str], ...] = (
    ("LP_OR_MLP_UNIT", r"limited partner(ship)? interests?|partnership units?|\bcommon units?\b"),
    ("NONCOMMON_TRUST_OR_UNIT", r"units? of beneficial interest|\btrust units?\b"),
    ("DEPOSITARY_PREFERRED", r"depositary shares?.*\bpreferred\b|\bpreferred\b.*depositary"),
    ("PREFERRED", r"\bpreferred\b|\bpreference shares?\b"),
    ("NOTE_OR_DEBT", r"\bnotes?\b|\bdebentures?\b|\bbonds?\b|\bsenior\b|\bsubordinated\b"),
    ("WARRANT", r"\bwarrants?\b|\badws?\b"),
    ("RIGHT", r"\bcontingent value rights?\b|\brights?\b"),
    ("UNIT", r"\bsub-?units?\b|\bcorporate units?\b|\btangible equity units?\b|\bunits?\b"),
)

# v4 whole-name overrides, in order (O1, O4, O6, O7).
_WHOLE_NAME_OVERRIDES: tuple[tuple[str, str], ...] = (
    (r"\btest (stock|issue|symbol)\b", "TEST_ISSUE"),
    (r"\bexchange[- ]traded (fund|note)s?\b|\betf\b|\betn\b", "ETF"),
    (r"\btracking stock\b", "TRACKING_STOCK"),
    (r"\bwhen[- ](issued|distributed)\b", "WHEN_ISSUED"),
)

# Appendix A1 / A5 shared patterns.
_TERM = (
    r"\b(Inc|Incorporated|Corp|Corporation|Ltd|Limited|plc|PLC|N\.V|S\.A|"
    r"L\.P|LLC|Co|Company|Trust|Group|Holdings|AG|SE)\b\.?,?"
)
_REF = (
    r",?\s*\b(each|representing|represents|exercisable|consisting|to "
    r"(purchase|acquire|buy|subscribe)|convertible (into|for)|included as "
    r"part of|underlying|entitling)\b"
)
_DEPOSITARY = (
    r"american deposit[ao]ry (shares?|receipts?)|\bads\b|\badrs?\b|"
    r"sponsored adr|\bdepositary shares?\b"
)
_AMERICAN_DEPOSITARY = (
    r"american deposit[ao]ry (shares?|receipts?)|\bads\b|\badrs?\b|sponsored adr"
)
_REIT = r"\breit\b|real estate investment trust"
_CLASS_WORD = (
    r"\s+(?=(?:\d+(?:\.\d+)?%|common|ordinary|class [a-z]\b|units?\b|"
    r"warrants?\b|rights?\b|preferred|notes?\b|shares?\b|depositary|"
    r"american depositary|series [a-z]\b|reit\b|closed end fund|"
    r"business development company))"
)

_SUFFIX_TOKENS = frozenset(
    {
        "inc",
        "incorporated",
        "corp",
        "corporation",
        "co",
        "company",
        "ltd",
        "limited",
        "llc",
        "lp",
        "plc",
        "sa",
        "nv",
        "ag",
        "se",
        "the",
    }
)

# Ticker-suffix exclusion evidence (v1 §3, carried into v3/v4).  These are
# exclusion-only: they never promote to COMMON_STOCK.
_FIFTH_LETTER_SUFFIX: dict[str, str] = {
    "W": "WARRANT",
    "R": "RIGHT",
    "U": "UNIT",
    "P": "PREFERRED",
    "M": "PREFERRED",
    "N": "PREFERRED",
    "O": "PREFERRED",
    "G": "NOTE_OR_DEBT",
    "H": "NOTE_OR_DEBT",
    "I": "NOTE_OR_DEBT",
    "V": "WHEN_ISSUED",
}

_EXCHANGE_CODE_VENUE: dict[str, str] = {
    "N": "NYSE",
    "A": "NYSE MKT",
    "P": "ARCA",
    "Z": "BATS",
    "V": "IEX",
}
_NASDAQTRADED_EXCHANGE_VENUE: dict[str, str] = {
    "Q": "NASDAQ",
    "N": "NYSE",
    "A": "NYSE MKT",
    "P": "ARCA",
    "Z": "BATS",
    "V": "IEX",
}

# Reserve firewall: no output may carry one of these fields.
FORBIDDEN_FIELDS = frozenset(
    {
        "open",
        "high",
        "low",
        "close",
        "adjopen",
        "adjhigh",
        "adjlow",
        "adjclose",
        "divcash",
        "splitfactor",
        "volume",
        "adjvolume",
        "return",
        "returns",
        "liquidity",
        "factor",
        "performance",
    }
)

_STRING = pd.ArrowDtype(pa.string())
_DATE32 = pd.ArrowDtype(pa.date32())
_INT64 = pd.ArrowDtype(pa.int64())
_BOOL = pd.ArrowDtype(pa.bool_())


# ---------------------------------------------------------------------------
# Typed errors (fail closed)
# ---------------------------------------------------------------------------


class ClassificationError(Exception):
    """Base class for every D-SC fail-closed error."""


class ClassificationInputError(ClassificationError, ValueError):
    """A required input is missing, unreadable or malformed."""


class ClassificationBindingError(ClassificationError, ValueError):
    """A frozen document or input-file hash does not match its binding."""


class ClassificationNondeterministicError(ClassificationError):
    """An existing output directory holds different bytes for the same id."""


# ---------------------------------------------------------------------------
# Canonical JSON and hashing
# ---------------------------------------------------------------------------


def canonical_json(obj: object) -> str:
    """The frozen canonical JSON spelling: sorted keys, tight separators, ASCII."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _canonical_bytes(obj: object) -> bytes:
    return (canonical_json(obj) + "\n").encode("utf-8")


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _file_sha256(path: Path) -> str:
    return _sha256_hex(path.read_bytes())


# ---------------------------------------------------------------------------
# Appendix A1 — string grammar (dsc_v4_grammar).  Copied verbatim.
# ---------------------------------------------------------------------------


def _classify_string(name: str | None) -> tuple[str, str]:
    n = re.sub(r"\s+", " ", name or "").strip()
    for pattern, reason in _WHOLE_NAME_OVERRIDES:
        if re.search(pattern, n, re.I):
            return ("NON", reason)
    dash = " - " in n
    cand = n.rsplit(" - ", 1)[1] if dash else n
    m = re.search(_REF, cand, re.I)
    pre = cand[: m.start()] if m else cand
    ref = cand[m.end() :] if m else ""
    if not dash:
        ms = list(re.finditer(_TERM, pre))
        inst = pre[ms[-1].end() :] if ms else pre
    else:
        inst = pre
    inst = inst.strip(" -,")
    if not inst:
        return ("UNR", "ISSUER_ONLY")
    for reason, pattern in _NON_INSTRUMENT:
        if re.search(pattern, inst, re.I):
            return ("NON", reason)
    if re.search(_DEPOSITARY, inst, re.I):
        if re.search(r"preferred|preference", ref, re.I):
            return ("NON", "DEPOSITARY_PREFERRED")
        if re.search(r"\b(common (stock|shares?)|ordinary (shares?|equity))\b", ref, re.I):
            return ("COM", "ADR_ADS")
        if re.search(_AMERICAN_DEPOSITARY, inst, re.I):
            return ("UNR", "BARE_ADS")
        return ("UNR", "GENERIC_SHARES")
    if re.search(r"beneficial interest", inst, re.I):
        if re.search(_REIT, n, re.I) and re.search(r"\bcommon\b", inst, re.I):
            return ("COM", "REIT_COMMON")
        return ("UNR", "SBI_AMBIGUOUS")
    if re.search(
        r"\bseries [a-z] (?!common\b)[a-z][\w&.\- ]* common (stock|shares?)\b",
        inst,
        re.I,
    ):
        return ("UNR", "POSSIBLE_TRACKING_STOCK")
    if re.search(r"\b(new )?common (stock|shares?)\b|\bordinary shares?\b", inst, re.I):
        if re.search(_REIT, n, re.I):
            return ("COM", "REIT_COMMON")
        return ("COM", "COMMON")
    if re.search(r"\bsubordinate voting shares\b|\bclass [a-z] shares\b|\bshares\b", inst, re.I):
        return ("UNR", "GENERIC_SHARES")
    if not re.search(r"\b(stock|shares?|units?|notes?|interests?)\b", inst, re.I):
        return ("UNR", "ISSUER_ONLY")
    return ("UNR", "UNRECOGNIZED")


def classify_string(name: str | None) -> tuple[str, str]:
    """Public A1 wrapper: ``(state ∈ {COM, NON, UNR}, reason)``."""
    return _classify_string(name)


# Alias kept for readability in the test-suite and in the report.
string_class = classify_string


# ---------------------------------------------------------------------------
# Appendix A2 — PIT class timeline (state_at).  Semantics preserved.
# ---------------------------------------------------------------------------


def _state_at(
    points: Sequence[tuple[date, str, str]],
    cutoff: date,
    static_test: bool = False,
    suffix: str | None = None,
    mism_dates: Iterable[date] = (),
    unres: Sequence[tuple[date, str]] = (),
) -> tuple[str, str]:
    """``points``: sorted ``(date, G ∈ {COM, NON}, detail)``.

    Returns ``(state, detail)``.  Exactly Appendix A2.
    """
    if static_test:
        return ("NON_COMMON", "TEST_ISSUE")
    pts = [p for p in points if p[0] <= cutoff]
    if not pts:
        if suffix:
            return ("NON_COMMON", suffix)
        if any(d <= cutoff for d in mism_dates):
            return ("CLASS_UNRESOLVED", "EXCHANGE_MISMATCH_REVIEW")
        u = [r for d, r in unres if d <= cutoff]
        return ("CLASS_UNRESOLVED", u[-1] if u else "NO_ADMISSIBLE_EVIDENCE")
    byd: dict[date, set[str]] = {}
    for d, g, _ in pts:
        byd.setdefault(d, set()).add(g)
    if any(len(s) > 1 for s in byd.values()):
        return ("EVIDENCE_CONFLICT", "CONCURRENT")
    runs: list[tuple[str, list[tuple[date, str]]]] = []
    for d, g, det in pts:
        if runs and runs[-1][0] == g:
            runs[-1][1].append((d, det))
        else:
            runs.append((g, [(d, det)]))
    if len(runs) >= 3:
        return ("EVIDENCE_CONFLICT", "ALTERNATING")
    g, run = runs[-1]
    if g == "COM":
        dets = {x for _, x in run}
        det = (
            "ADR_ADS"
            if "ADR_ADS" in dets
            else "REIT_COMMON"
            if "REIT_COMMON" in dets
            else "COMMON"
        )
        if suffix:
            return ("EVIDENCE_CONFLICT", "SUFFIX_CONTRADICTION")
        return ("COMMON_STOCK", det)
    return ("NON_COMMON", run[-1][1])


def state_at(
    points: Sequence[tuple[date, str, str]],
    cutoff: date,
    static_test: bool = False,
    suffix: str | None = None,
    mism_dates: Iterable[date] = (),
    unres: Sequence[tuple[date, str]] = (),
) -> tuple[str, str]:
    """Public A2 wrapper (accepts the same arguments)."""
    return _state_at(points, cutoff, static_test, suffix, mism_dates, unres)


# ---------------------------------------------------------------------------
# Appendix A3 — when-issued parallel-line filter + SEC-form status machine
# ---------------------------------------------------------------------------


def _v3_points(
    points: Sequence[tuple[date, str, str, str]],
) -> tuple[list[tuple[date, str, str, str]], list[tuple[date, str, str, str]]]:
    """Drop/flag WHEN_ISSUED points dated on/after any COMMON point.

    ``points`` are ``(date, G, detail, evidence_id)``; the return carries the
    same tuples so the flagged evidence rows can be marked.
    """
    com = [d for d, g, _, _ in points if g == "COM"]
    first = min(com) if com else None
    kept: list[tuple[date, str, str, str]] = []
    flag: list[tuple[date, str, str, str]] = []
    for p in points:
        if p[1] == "NON" and p[2] == "WHEN_ISSUED" and first is not None and p[0] >= first:
            flag.append(p)
        else:
            kept.append(p)
    return kept, flag


def v3_points(
    points: Sequence[tuple[date, str, str]],
) -> tuple[list[tuple[date, str, str]], list[tuple[date, str, str]]]:
    """Public A3 wrapper on ``(date, G, detail)`` tuples."""
    com = [d for d, g, _ in points if g == "COM"]
    first = min(com) if com else None
    kept = []
    flag = []
    for p in points:
        if p[1] == "NON" and p[2] == "WHEN_ISSUED" and first is not None and p[0] >= first:
            flag.append(p)
        else:
            kept.append(p)
    return kept, flag


def _sec_overlay(
    events: Sequence[tuple[date, str]],
    cutoff: date,
    ambiguous: bool = False,
) -> tuple[str, str | None]:
    if ambiguous:
        return ("ISSUER_STATUS_UNRESOLVED", "AMBIGUOUS_MAPPING")
    ev = [e for e in events if e[0] <= cutoff]
    bdc = [e for e in ev if e[1] in ("N-54A", "N-54C")]
    last = max(bdc)[1] if bdc else None
    if last == "N-54A":
        return ("BDC", "N-54A")
    if any(f == "N-8A" for _, f in ev):
        return ("REGISTERED_FUND", "N-8A")
    if last == "N-54C":
        return ("ISSUER_STATUS_UNRESOLVED", "WITHDRAWN_NO_SUCCESSOR_STATUS")
    return ("NO_REGULATORY_EXCLUSION", None)


def sec_overlay(
    events: Sequence[tuple[date, str]],
    cutoff: date,
    ambiguous: bool = False,
) -> tuple[str, str | None]:
    """Public A3 ``overlay`` wrapper."""
    return _sec_overlay(events, cutoff, ambiguous)


# ---------------------------------------------------------------------------
# Appendix A4 — directory-name overlay + combination
# ---------------------------------------------------------------------------


def name_evidence(name: str, etf_flag: str) -> str | None:
    if etf_flag == "Y":
        return None
    if re.search(r"\bbusiness development (company|corporation)\b", name, re.I) or re.search(
        r"\bBDC\b", name
    ):
        return "NAME_BASED_BDC"
    if re.search(r"\bclosed[- ]end fund\b", name, re.I) or re.search(r"\bfund\b", name, re.I):
        return "NAME_BASED_REGISTERED_FUND"
    return None


def _policy(
    cut: date,
    sec_events: Sequence[tuple[date, str]],
    ambiguous: bool,
    name_pts: Sequence[tuple[date, str]],
) -> tuple[str, frozenset[str]]:
    s, why = _sec_overlay(sec_events, cut, ambiguous)
    src: set[str] = set()
    if s == "BDC":
        src.add("SEC_FORM_BDC")
    if s == "REGISTERED_FUND":
        src.add("SEC_FORM_REGISTERED_FUND")
    if not ambiguous and any(f == "N-8A" for d, f in sec_events if d <= cut):
        src.add("SEC_FORM_REGISTERED_FUND")
    for d, k in name_pts:
        if d <= cut:
            src.add(k)
    if src:
        return ("EXCLUDED", frozenset(src))
    if s == "ISSUER_STATUS_UNRESOLVED":
        return ("ISSUER_STATUS_UNRESOLVED", frozenset([why]))
    return ("NO_REGULATORY_EXCLUSION", frozenset())


def policy(
    cut: date,
    sec_events: Sequence[tuple[date, str]],
    ambiguous: bool,
    name_pts: Sequence[tuple[date, str]],
) -> tuple[str, frozenset[str]]:
    """Public A4 ``policy`` wrapper (returns ``NO_REGULATORY_EXCLUSION``)."""
    return _policy(cut, sec_events, ambiguous, name_pts)


# ---------------------------------------------------------------------------
# Appendix A5 — issuer-name extraction and normalization for SEC mapping
# ---------------------------------------------------------------------------


def norm(s: str | None) -> str:
    s = (s or "").lower().replace("&", " and ")
    s = re.sub(r"[/\\][a-z]{2,3}[/\\]?\s*$", "", s.strip())
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    t = s.split()
    if t and t[0] == "the":
        t = t[1:]
    while t and t[-1] in _SUFFIX_TOKENS:
        t = t[:-1]
    t = [x for x in t if x not in ("l", "p")] if t[-2:] == ["l", "p"] else t
    return " ".join(t)


def issuer(name: str) -> str:
    n = re.sub(r"\s+", " ", name).strip()
    if " - " in n:
        return n.rsplit(" - ", 1)[0]
    m = re.search(_REF, n, re.I)
    p = n[: m.start()] if m else n
    ms = list(re.finditer(_TERM, p))
    if ms:
        return p[: ms[-1].end()]
    return re.split(_CLASS_WORD, p, maxsplit=1, flags=re.I)[0]


# ---------------------------------------------------------------------------
# Suffix evidence
# ---------------------------------------------------------------------------


def _segment_suffix(seg: str) -> str | None:
    s = seg.upper()
    if s == "W":
        return "WARRANT"
    if s == "U":
        return "UNIT"
    if s in ("R", "RT"):
        return "RIGHT"
    if s == "WI":
        return "WHEN_ISSUED"
    if s.startswith("WS"):
        return "WARRANT"
    if s.startswith("P"):
        return "PREFERRED"
    return None


def suffix_reason(ticker: str, exchange: str) -> str | None:
    """The applicable NON_COMMON suffix reason, or ``None``.

    A 5-letter alphabetic NASDAQ symbol is read by its fifth letter; otherwise
    the dash segments are read left to right (v1 §3, v3/v4 carried).
    """
    if exchange == "NASDAQ" and re.fullmatch(r"[A-Za-z]{5}", ticker or ""):
        return _FIFTH_LETTER_SUFFIX.get(ticker[4].upper())
    if ticker and "-" in ticker:
        for seg in ticker.split("-")[1:]:
            reason = _segment_suffix(seg)
            if reason:
                return reason
    return None


# ---------------------------------------------------------------------------
# Capture parsing
# ---------------------------------------------------------------------------


def _parse_sha256sums(path: Path) -> dict[str, str]:
    if not path.is_file():
        raise ClassificationInputError(f"missing SHA256SUMS at {path}")
    mapping: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line:
            continue
        # "<sha256>  <filename>" with an optional leading "*" on the filename.
        parts = line.split()
        if len(parts) < 2:
            continue
        digest = parts[0].strip().lower()
        name = " ".join(parts[1:]).lstrip("*").strip()
        mapping[name] = digest
    return mapping


def _split_capture_row(line: str) -> list[str]:
    return line.split("|")


def _parse_capture_file(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="latin-1")
    lines = text.split("\n")
    header: list[str] | None = None
    records: list[dict[str, Any]] = []
    data_index = -1
    match = _CAPTURE_RE.match(path.name)
    assert match is not None
    kind = match.group(1)
    timestamp = match.group(2)
    capture_date = date(
        int(timestamp[0:4]), int(timestamp[4:6]), int(timestamp[6:8])
    )
    for raw in lines:
        line = raw.rstrip("\r")
        if not line.strip():
            continue
        if header is None:
            header = _split_capture_row(line)
            continue
        data_index += 1
        if line.startswith("File Creation Time"):
            continue
        fields = _split_capture_row(line)
        record = {
            "kind": kind,
            "capture_date": capture_date,
            "source_file": path.name,
            "source_row": data_index,
            "fields": fields,
            "header": header,
        }
        records.append(record)
    if header is None:
        raise ClassificationInputError(f"capture {path.name} has no header")
    return {"kind": kind, "capture_date": capture_date, "header": header, "rows": records}


def _field(header: Sequence[str], fields: Sequence[str], name: str) -> str | None:
    try:
        index = list(header).index(name)
    except ValueError:
        return None
    if index >= len(fields):
        return None
    return fields[index]


def _capture_symbol_venue(
    kind: str, header: Sequence[str], fields: Sequence[str]
) -> tuple[str | None, str | None]:
    if kind == "nasdaqlisted":
        symbol = _field(header, fields, "Symbol")
        venue: str | None = "NASDAQ"
    elif kind == "otherlisted":
        symbol = _field(header, fields, "ACT Symbol")
        raw_exchange = _field(header, fields, "Exchange")
        venue = _EXCHANGE_CODE_VENUE.get((raw_exchange or "").strip().upper())
    else:  # nasdaqtraded
        symbol = _field(header, fields, "Symbol")
        raw_exchange = _field(header, fields, "Listing Exchange")
        venue = _NASDAQTRADED_EXCHANGE_VENUE.get((raw_exchange or "").strip().upper())
    if symbol is not None:
        symbol = symbol.strip().replace(".", "-")
    return (symbol or None, venue)


def _capture_flags(header: Sequence[str], fields: Sequence[str]) -> tuple[str, str]:
    etf = (_field(header, fields, "ETF") or "N").strip()
    test = (_field(header, fields, "Test Issue") or "N").strip()
    return (etf.upper(), test.upper())


def _venue_admissible(venue: str | None, exchange: str) -> bool:
    if venue is None:
        return False
    if venue == exchange:
        return True
    if exchange in ("AMEX", "NYSE MKT") and venue == "NYSE MKT":
        return True
    return False


# ---------------------------------------------------------------------------
# SEC full-index parsing
# ---------------------------------------------------------------------------


def _split_sec_row(line: str) -> tuple[str, str, str, str, str] | None:
    parts = line.split("|")
    if len(parts) < 5:
        return None
    cik = parts[0].strip()
    filename = parts[-1].strip()
    date_text = parts[-2].strip()
    form = parts[-3].strip()
    company = "|".join(parts[1:-3]).strip()
    if not cik or not form:
        return None
    try:
        filed = date.fromisoformat(date_text[:10])
    except ValueError:
        return None
    return (cik, company, form, filed.isoformat(), filename)


def _iter_master_rows(path: Path) -> Iterable[tuple[str, str, str, str, str]]:
    raw = path.read_bytes()
    try:
        text = gzip.decompress(raw).decode("latin-1")
    except OSError as exc:  # pragma: no cover - corrupt input
        raise ClassificationInputError(f"cannot decompress {path.name}: {exc}") from exc
    started = False
    for line in text.split("\n"):
        if not started:
            if line.startswith("-----"):
                started = True
            continue
        line = line.rstrip("\r")
        if not line.strip():
            continue
        row = _split_sec_row(line)
        if row is not None:
            yield row


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


class _Key:
    __slots__ = (
        "security_key",
        "ticker",
        "exchange",
        "listed_start",
        "listed_end",
        "evidence",
        "points",
        "unres",
        "mism",
        "name_pts",
        "issuer_names",
    )

    def __init__(
        self,
        security_key: str,
        ticker: str,
        exchange: str,
        listed_start: date,
        listed_end: date,
    ) -> None:
        self.security_key = security_key
        self.ticker = ticker
        self.exchange = exchange
        self.listed_start = listed_start
        self.listed_end = listed_end
        self.evidence: list[dict[str, Any]] = []
        self.points: list[tuple[date, str, str, str]] = []
        self.unres: list[tuple[date, str]] = []
        self.mism: list[tuple[date, str]] = []
        self.name_pts: list[tuple[date, str, str]] = []
        self.issuer_names: set[str] = set()


# ---------------------------------------------------------------------------
# Segment construction
# ---------------------------------------------------------------------------


def _runs_and_conflict(
    points: Sequence[tuple[date, str, str]],
) -> tuple[int, bool]:
    if not points:
        return (0, False)
    byd: dict[date, set[str]] = {}
    for d, g, _ in points:
        byd.setdefault(d, set()).add(g)
    concurrent = any(len(s) > 1 for s in byd.values())
    runs: list[str] = []
    for _, g, _ in points:
        if not runs or runs[-1] != g:
            runs.append(g)
    return (len(runs), concurrent or len(runs) >= 3)


def _class_segments(key: _Key) -> list[dict[str, Any]]:
    static_test = key.ticker in TESTSYM
    suffix = suffix_reason(key.ticker, key.exchange)
    points = sorted(key.points, key=lambda p: (p[0], p[1], p[2], p[3]))
    kept, flagged = _v3_points(points)
    flagged_ids = {p[3] for p in flagged}
    for row in key.evidence:
        if row["evidence_id"] in flagged_ids:
            row["when_issued_parallel"] = True

    trimmed = [(d, g, det) for d, g, det, _ in kept]
    mism_dates = sorted({d for d, _ in key.mism})
    unres_sorted = sorted(key.unres, key=lambda p: (p[0], p[1]))

    candidate_dates = {key.listed_start}
    for row in key.evidence:
        if row["capture_date"] >= key.listed_start:
            candidate_dates.add(row["capture_date"])
    # SEC dates never change security class; they only bound the policy
    # timeline, which is built separately.

    def ids_le(cutoff: date) -> list[str]:
        return sorted(
            row["evidence_id"]
            for row in key.evidence
            if row["capture_date"] <= cutoff
        )

    segments: list[dict[str, Any]] = []
    previous: tuple[str, str] | None = None
    for d in sorted(candidate_dates):
        state, reason = _state_at(
            trimmed, d, static_test, suffix, mism_dates, unres_sorted
        )
        if previous is None or (state, reason) != previous:
            segments.append(
                {
                    "knowledge_date": d,
                    "state": state,
                    "reason": reason,
                    "evidence_ids": ids_le(d),
                }
            )
            previous = (state, reason)
    return segments


def _policy_segments(
    key: _Key,
    sec_events: Sequence[tuple[date, str]],
    ambiguous: bool,
) -> list[dict[str, Any]]:
    name_pts = sorted(key.name_pts, key=lambda p: (p[0], p[1], p[2]))
    candidate_dates = {key.listed_start}
    for row in key.evidence:
        if row["capture_date"] >= key.listed_start:
            candidate_dates.add(row["capture_date"])
    # SEC events may be filed before the listing start; they are still
    # knowable and must carry their own policy segment so that a pre-listing
    # cutoff reflects only evidence dated on or before it.
    for d, _ in sec_events:
        candidate_dates.add(d)

    def refs(cutoff: date, sources: frozenset[str]) -> list[str]:
        out: set[str] = set()
        if "NAME_BASED_BDC" in sources:
            out.update(
                row["evidence_id"]
                for row in key.evidence
                if row["name_overlay"] == "NAME_BASED_BDC"
                and row["venue_admissible"]
                and row["capture_date"] <= cutoff
            )
        if "NAME_BASED_REGISTERED_FUND" in sources:
            out.update(
                row["evidence_id"]
                for row in key.evidence
                if row["name_overlay"] == "NAME_BASED_REGISTERED_FUND"
                and row["venue_admissible"]
                and row["capture_date"] <= cutoff
            )
        if "SEC_FORM_BDC" in sources:
            out.update(
                f"SEC:{key.security_key}:{form}:{d.isoformat()}"
                for d, form in sec_events
                if form in ("N-54A", "N-54C") and d <= cutoff
            )
        if "SEC_FORM_REGISTERED_FUND" in sources:
            out.update(
                f"SEC:{key.security_key}:{form}:{d.isoformat()}"
                for d, form in sec_events
                if form == "N-8A" and d <= cutoff
            )
        return sorted(out)

    segments: list[dict[str, Any]] = []
    previous: tuple[str, tuple[str, ...]] | None = None
    for d in sorted(candidate_dates):
        status, sources = _policy(d, sec_events, ambiguous, [(p[0], p[1]) for p in name_pts])
        label = (
            "NO_REGULATORY_EXCLUSION_OBSERVED"
            if status == "NO_REGULATORY_EXCLUSION"
            else status
        )
        key_state = (label, tuple(sorted(sources)))
        if previous is None or key_state != previous:
            segments.append(
                {
                    "knowledge_date": d,
                    "policy_state": label,
                    "active_sources": sorted(sources),
                    "evidence_refs": refs(d, sources),
                }
            )
            previous = key_state
    return segments


# ---------------------------------------------------------------------------
# Frame builders
# ---------------------------------------------------------------------------


def _frame(rows: Sequence[Mapping[str, Any]], dtypes: Mapping[str, Any]) -> pd.DataFrame:
    data = {
        column: pd.Series([row.get(column) for row in rows], dtype=dtype)
        for column, dtype in dtypes.items()
    }
    return pd.DataFrame(data)


_EVIDENCE_DTYPES = {
    "evidence_id": _STRING,
    "security_key": _STRING,
    "source_file": _STRING,
    "source_row": _INT64,
    "capture_date": _DATE32,
    "venue": _STRING,
    "venue_admissible": _BOOL,
    "security_name": _STRING,
    "etf_flag": _STRING,
    "test_flag": _STRING,
    "string_state": _STRING,
    "string_reason": _STRING,
    "name_overlay": _STRING,
    "when_issued_parallel": _BOOL,
}

_REGULATORY_DTYPES = {
    "cik": _STRING,
    "company_name": _STRING,
    "form": _STRING,
    "base_form": _STRING,
    "date_filed": _DATE32,
    "filename": _STRING,
    "source_index": _STRING,
}

_MAPPING_DTYPES = {
    "security_key": _STRING,
    "issuer_names_norm": _STRING,
    "candidate_ciks": _STRING,
    "mapping_state": _STRING,
}

_CLASS_TIMELINE_DTYPES = {
    "security_key": _STRING,
    "knowledge_date": _DATE32,
    "state": _STRING,
    "reason": _STRING,
    "evidence_ids": _STRING,
}

_POLICY_TIMELINE_DTYPES = {
    "security_key": _STRING,
    "knowledge_date": _DATE32,
    "policy_state": _STRING,
    "active_sources": _STRING,
    "evidence_refs": _STRING,
}

_UNRESOLVED_DTYPES = {
    "security_key": _STRING,
    "class_state": _STRING,
    "class_reason": _STRING,
    "policy_state": _STRING,
    "policy_reason": _STRING,
}


# ---------------------------------------------------------------------------
# Timeline query helpers
# ---------------------------------------------------------------------------


def class_state_at(
    timeline: Mapping[str, Sequence[Mapping[str, Any]]],
    key: str,
    cutoff: date,
) -> tuple[str, str]:
    """Answer the class of ``key`` at query cutoff ``cutoff`` from segments.

    A segment applies to cutoffs ``>=`` its ``knowledge_date``.  When no
    segment is knowable at ``cutoff`` (``cutoff`` earlier than the listing
    start, or the key has no timeline) the state is the empty-evidence state;
    the first future segment is never exposed.
    """
    segments = timeline.get(key)
    chosen: Mapping[str, Any] | None = None
    for segment in segments or ():
        if segment["knowledge_date"] <= cutoff:
            chosen = segment
        else:
            break
    if chosen is None:
        return ("CLASS_UNRESOLVED", "NO_ADMISSIBLE_EVIDENCE")
    return (str(chosen["state"]), str(chosen["reason"]))


def policy_state_at(
    timeline: Mapping[str, Sequence[Mapping[str, Any]]],
    key: str,
    cutoff: date,
) -> tuple[str, frozenset[str]]:
    """Answer the policy status of ``key`` at query cutoff ``cutoff``.

    A segment applies to cutoffs ``>=`` its ``knowledge_date``.  When no
    segment is knowable at ``cutoff`` (no SEC or directory-name evidence on
    or before ``cutoff``) the status is ``NO_REGULATORY_EXCLUSION_OBSERVED``
    with no sources; the first future segment is never exposed.  A SEC event
    or name capture dated on or before ``cutoff`` -- including one filed
    before the listing start -- is represented by its own segment.
    """
    segments = timeline.get(key)
    chosen: Mapping[str, Any] | None = None
    for segment in segments or ():
        if segment["knowledge_date"] <= cutoff:
            chosen = segment
        else:
            break
    if chosen is None:
        return ("NO_REGULATORY_EXCLUSION_OBSERVED", frozenset())
    sources = chosen.get("active_sources")
    if isinstance(sources, str):
        sources = json.loads(sources) if sources else []
    return (str(chosen["policy_state"]), frozenset(sources or []))


def load_class_timeline(path: str | Path) -> dict[str, list[dict[str, Any]]]:
    frame = pd.read_parquet(path)
    mapping: dict[str, list[dict[str, Any]]] = {}
    for record in frame.to_dict("records"):
        mapping.setdefault(record["security_key"], []).append(record)
    return mapping


def load_policy_timeline(path: str | Path) -> dict[str, list[dict[str, Any]]]:
    frame = pd.read_parquet(path)
    mapping: dict[str, list[dict[str, Any]]] = {}
    for record in frame.to_dict("records"):
        mapping.setdefault(record["security_key"], []).append(record)
    return mapping


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------


def _as_date(value: Any, label: str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        text = value.strip()
        if text:
            try:
                return date.fromisoformat(text[:10])
            except ValueError:
                pass
    raise ClassificationInputError(f"{label} must be a valid ISO date, got {value!r}")


def _verify_frozen_document(path: Path, expected: str, label: str) -> str:
    if not path.is_file():
        raise ClassificationInputError(f"missing {label} at {path}")
    digest = _file_sha256(path)
    if digest != expected:
        raise ClassificationBindingError(
            f"{label} SHA-256 {digest} does not equal the frozen value {expected}"
        )
    return digest


# ---------------------------------------------------------------------------
# Core classifier
# ---------------------------------------------------------------------------


def classify(
    root: str | Path,
    identity_id: str,
    *,
    captures_dir: str | Path | None = None,
    sec_index_dir: str | Path | None = None,
    amendment_path: str | Path | None = None,
    vocabulary_path: str | Path | None = None,
) -> dict[str, Any]:
    """Classify every D4 ``INTERVAL_ASSIGNED`` key and write the D-SC artifact.

    Returns the ``dsc.json`` content.  Outputs are written under
    ``<root>/derived/classification/<dsc_id>/``.  Nothing is overwritten and
    no provider, network or real-``data_store`` access is performed.
    """
    root_path = Path(root)
    captures_path = (
        Path(captures_dir)
        if captures_dir is not None
        else root_path / "dsc_evidence" / "wayback-20260927"
    )
    sec_path = (
        Path(sec_index_dir)
        if sec_index_dir is not None
        else root_path / "dsc_evidence" / "sec-regindex-20260927" / "raw"
    )
    amendment_file = (
        Path(amendment_path) if amendment_path is not None else _DEFAULT_AMENDMENT_PATH
    )
    vocabulary_file = (
        Path(vocabulary_path) if vocabulary_path is not None else _DEFAULT_VOCABULARY_PATH
    )

    amendment_sha = _verify_frozen_document(amendment_file, AMENDMENT_SHA256, "amendment")
    vocabulary_sha = _verify_frozen_document(
        vocabulary_file, VOCABULARY_SHA256, "vocabulary"
    )

    # --- D4 identity run -------------------------------------------------
    identity_dir = root_path / "derived" / "identity" / identity_id
    identity_json_path = identity_dir / "identity.json"
    assignment_path = identity_dir / "identity_assignment.parquet"
    if not identity_json_path.is_file() or not assignment_path.is_file():
        raise ClassificationInputError(f"D4 identity run {identity_dir} is incomplete")
    identity = json.loads(identity_json_path.read_text(encoding="utf-8"))
    if identity.get("identity_id") != identity_id:
        raise ClassificationInputError(
            f"identity.json identity_id {identity.get('identity_id')!r} does not "
            f"match requested {identity_id!r}"
        )
    identity_files_sha256 = identity.get("files_sha256")
    if not isinstance(identity_files_sha256, Mapping):
        raise ClassificationInputError("identity.json has no files_sha256 map")
    assignment_key = f"derived/identity/{identity_id}/identity_assignment.parquet"
    assignment_sha = _file_sha256(assignment_path)
    if identity_files_sha256.get(assignment_key) != assignment_sha:
        raise ClassificationBindingError(
            "identity_assignment.parquet SHA-256 does not match identity.json files_sha256"
        )
    identity_hash = str(identity.get("identity_hash", ""))

    assignment = pd.read_parquet(
        assignment_path,
        columns=[
            "security_key",
            "ticker",
            "exchange",
            "listed_start",
            "listed_end",
            "state",
        ],
    )
    assigned = assignment[assignment["state"] == "INTERVAL_ASSIGNED"].copy()
    assigned = assigned.sort_values("security_key", kind="stable")

    keys: dict[str, _Key] = {}
    for record in assigned.to_dict("records"):
        security_key = str(record["security_key"])
        keys[security_key] = _Key(
            security_key,
            str(record["ticker"]),
            str(record["exchange"]),
            _as_date(record["listed_start"], "listed_start"),
            _as_date(record["listed_end"], "listed_end"),
        )

    # --- Wayback captures ------------------------------------------------
    captures_sha256sums = _parse_sha256sums(captures_path / "SHA256SUMS")
    captures_files_sha256: dict[str, str] = {}
    capture_meta: list[dict[str, Any]] = []
    capture_files = sorted(
        p for p in captures_path.iterdir() if p.is_file() and _CAPTURE_RE.match(p.name)
    )
    for path in capture_files:
        digest = _file_sha256(path)
        expected = captures_sha256sums.get(path.name)
        if expected is None:
            raise ClassificationBindingError(
                f"capture {path.name} has no line in SHA256SUMS"
            )
        if digest != expected:
            raise ClassificationBindingError(
                f"capture {path.name} SHA-256 {digest} does not match SHA256SUMS "
                f"{expected}"
            )
        captures_files_sha256[path.name] = digest
        capture_meta.append(_parse_capture_file(path))

    keys_by_ticker: dict[str, list[_Key]] = {}
    for key in keys.values():
        keys_by_ticker.setdefault(key.ticker, []).append(key)

    for capture in capture_meta:
        header = capture["header"]
        for row in capture["rows"]:
            fields = row["fields"]
            symbol, venue = _capture_symbol_venue(capture["kind"], header, fields)
            if symbol is None:
                continue
            etf, test = _capture_flags(header, fields)
            security_name = (_field(header, fields, "Security Name") or "").strip()
            for key in keys_by_ticker.get(symbol, []):
                if not (key.listed_start <= capture["capture_date"] <= key.listed_end):
                    continue
                admissible = _venue_admissible(venue, key.exchange)
                string_state, string_reason = (
                    ("NON", "TEST_ISSUE")
                    if test == "Y"
                    else ("NON", "ETF")
                    if etf == "Y"
                    else _classify_string(security_name)
                )
                overlay = name_evidence(security_name, etf)
                evidence_id = "e-" + _sha256_hex(
                    canonical_json(
                        [key.security_key, row["source_file"], row["source_row"]]
                    ).encode("utf-8")
                )[:16]
                evidence_row = {
                    "evidence_id": evidence_id,
                    "security_key": key.security_key,
                    "source_file": row["source_file"],
                    "source_row": row["source_row"],
                    "capture_date": capture["capture_date"],
                    "venue": venue,
                    "venue_admissible": admissible,
                    "security_name": security_name,
                    "etf_flag": etf,
                    "test_flag": test,
                    "string_state": string_state,
                    "string_reason": string_reason,
                    "name_overlay": overlay,
                    "when_issued_parallel": False,
                }
                key.evidence.append(evidence_row)
                if admissible:
                    if string_state in ("COM", "NON"):
                        key.points.append(
                            (
                                capture["capture_date"],
                                string_state,
                                string_reason,
                                evidence_id,
                            )
                        )
                    else:
                        key.unres.append((capture["capture_date"], string_reason))
                    name = issuer(security_name)
                    if name:
                        key.issuer_names.add(norm(name))
                    if overlay is not None:
                        key.name_pts.append(
                            (capture["capture_date"], overlay, evidence_id)
                        )
                else:
                    if string_state in ("COM", "NON"):
                        key.mism.append((capture["capture_date"], evidence_id))

    # --- SEC full-index --------------------------------------------------
    relevant_names: set[str] = set()
    for key in keys.values():
        relevant_names.update(key.issuer_names)
    (
        regulatory_events,
        name_to_ciks,
        sec_files_sha256,
    ) = _read_sec_index(sec_path, relevant_names)

    # --- Class and policy timelines -------------------------------------
    class_rows: list[dict[str, Any]] = []
    policy_rows: list[dict[str, Any]] = []
    mapping_rows: list[dict[str, Any]] = []
    unresolved_rows: list[dict[str, Any]] = []
    counts_class: dict[str, int] = {}
    counts_policy: dict[str, int] = {}
    n_transitions = 0

    for security_key in sorted(keys):
        key = keys[security_key]
        # Issuer mapping is deterministic and fail-closed (A5).
        candidate_ciks: set[str] = set()
        for name in key.issuer_names:
            candidate_ciks.update(name_to_ciks.get(name, set()))
        if not key.issuer_names:
            mapping_state = "NO_NAME"
            sec_events: list[tuple[date, str]] = []
            ambiguous = False
        elif len(candidate_ciks) == 0:
            mapping_state = "NO_MATCH"
            sec_events = []
            ambiguous = False
        elif len(candidate_ciks) == 1:
            mapping_state = "MAPPED"
            mapped_cik = next(iter(candidate_ciks))
            sec_events = sorted(
                (event["date_filed"], event["base_form"])
                for event in regulatory_events
                if event["cik"] == mapped_cik
            )
            ambiguous = False
        else:
            mapping_state = "AMBIGUOUS"
            sec_events = []
            ambiguous = True
        mapping_rows.append(
            {
                "security_key": security_key,
                "issuer_names_norm": canonical_json(sorted(key.issuer_names)),
                "candidate_ciks": canonical_json(sorted(candidate_ciks)),
                "mapping_state": mapping_state,
            }
        )

        segments = _class_segments(key)
        for segment in segments:
            class_rows.append(
                {
                    "security_key": security_key,
                    "knowledge_date": segment["knowledge_date"],
                    "state": segment["state"],
                    "reason": segment["reason"],
                    "evidence_ids": canonical_json(segment["evidence_ids"]),
                }
            )
        policy_segments = _policy_segments(key, sec_events, ambiguous)
        for segment in policy_segments:
            policy_rows.append(
                {
                    "security_key": security_key,
                    "knowledge_date": segment["knowledge_date"],
                    "policy_state": segment["policy_state"],
                    "active_sources": canonical_json(segment["active_sources"]),
                    "evidence_refs": canonical_json(segment["evidence_refs"]),
                }
            )

        kept = sorted(key.points, key=lambda p: (p[0], p[1], p[2], p[3]))
        kept, _ = _v3_points(kept)
        trimmed = [(d, g, det) for d, g, det, _ in kept]
        n_runs, conflict = _runs_and_conflict(trimmed)
        if n_runs == 2 and not conflict:
            n_transitions += 1

        cutoff = date.fromordinal(C.toordinal() - 1)
        class_map = {security_key: segments}
        policy_map = {security_key: policy_segments}
        c_state, c_reason = class_state_at(class_map, security_key, cutoff)
        p_state, _ = policy_state_at(policy_map, security_key, cutoff)
        counts_class[c_state] = counts_class.get(c_state, 0) + 1
        counts_policy[p_state] = counts_policy.get(p_state, 0) + 1
        if c_state in ("CLASS_UNRESOLVED", "EVIDENCE_CONFLICT") or p_state == (
            "ISSUER_STATUS_UNRESOLVED"
        ):
            unresolved_rows.append(
                {
                    "security_key": security_key,
                    "class_state": c_state,
                    "class_reason": c_reason,
                    "policy_state": p_state,
                    "policy_reason": canonical_json(
                        sorted(policy_state_at(policy_map, security_key, cutoff)[1])
                    ),
                }
            )

    evidence_rows = [
        row for key in keys.values() for row in key.evidence
    ]
    evidence_rows.sort(
        key=lambda row: (
            row["security_key"],
            row["capture_date"],
            row["source_file"],
            row["source_row"],
        )
    )

    regulatory_events.sort(
        key=lambda event: (
            event["cik"],
            event["date_filed"],
            event["form"],
            event["source_index"],
        )
    )
    mapping_rows.sort(key=lambda row: row["security_key"])
    class_rows.sort(key=lambda row: (row["security_key"], row["knowledge_date"]))
    policy_rows.sort(key=lambda row: (row["security_key"], row["knowledge_date"]))
    unresolved_rows.sort(key=lambda row: row["security_key"])

    evidence_frame = _frame(evidence_rows, _EVIDENCE_DTYPES)
    regulatory_frame = _frame(regulatory_events, _REGULATORY_DTYPES)
    mapping_frame = _frame(mapping_rows, _MAPPING_DTYPES)
    class_frame = _frame(class_rows, _CLASS_TIMELINE_DTYPES)
    policy_frame = _frame(policy_rows, _POLICY_TIMELINE_DTYPES)
    unresolved_frame = _frame(unresolved_rows, _UNRESOLVED_DTYPES)

    counts = {
        "n_interval_assigned_keys": len(keys),
        "n_evidence_rows": len(evidence_rows),
        "n_regulatory_events": len(regulatory_events),
        "n_genuine_transitions": n_transitions,
        "class_at_C": {
            name: counts_class.get(name, 0)
            for name in (
                "COMMON_STOCK",
                "NON_COMMON",
                "CLASS_UNRESOLVED",
                "EVIDENCE_CONFLICT",
            )
        },
        "policy_at_C": {
            name: counts_policy.get(name, 0)
            for name in (
                "EXCLUDED",
                "ISSUER_STATUS_UNRESOLVED",
                "NO_REGULATORY_EXCLUSION_OBSERVED",
            )
        },
    }

    # --- dsc_id and outputs ---------------------------------------------
    code_sha = _file_sha256(Path(__file__))
    input_hashes = {
        "identity": {
            "identity_id": identity_id,
            "identity_hash": identity_hash,
            "files_sha256": dict(identity_files_sha256),
        },
        "captures_sha256sums_sha256": _file_sha256(captures_path / "SHA256SUMS"),
        "captures_files_sha256": captures_files_sha256,
        "sec_index_files_sha256": sec_files_sha256,
    }
    dsc_payload = {
        "amendment_sha256": amendment_sha,
        "vocabulary_sha256": vocabulary_sha,
        "classification_code_sha256": code_sha,
        "inputs_sha256": input_hashes,
    }
    dsc_id = "c-" + _sha256_hex(canonical_json(dsc_payload).encode("utf-8"))[:16]

    output_dir = root_path / "derived" / "classification" / dsc_id
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(tempfile.mkdtemp(prefix=dsc_id + ".", dir=output_dir.parent))
    try:
        frames = {
            "evidence.parquet": evidence_frame,
            "regulatory_events.parquet": regulatory_frame,
            "issuer_mapping.parquet": mapping_frame,
            "class_timeline.parquet": class_frame,
            "policy_timeline.parquet": policy_frame,
            "unresolved_conflict.parquet": unresolved_frame,
        }
        files_sha256: dict[str, str] = {}
        for name, frame in frames.items():
            path = temp_dir / name
            frame.to_parquet(
                path, engine="pyarrow", index=False, compression="zstd"
            )
            files_sha256[f"derived/classification/{dsc_id}/{name}"] = _file_sha256(path)
        dsc_hash = _sha256_hex(canonical_json(files_sha256).encode("utf-8"))
        content: dict[str, Any] = {
            "schema": SCHEMA,
            "dsc_id": dsc_id,
            "C": C.isoformat(),
            "amendment_sha256": amendment_sha,
            "vocabulary_sha256": vocabulary_sha,
            "classification_code_sha256": code_sha,
            "identity_id": identity_id,
            "identity_hash": identity_hash,
            "identity_files_sha256": dict(identity_files_sha256),
            "captures_sha256sums_sha256": input_hashes["captures_sha256sums_sha256"],
            "captures_files_sha256": captures_files_sha256,
            "sec_index_files_sha256": sec_files_sha256,
            "counts": counts,
            "files_sha256": files_sha256,
            "dsc_hash": dsc_hash,
        }
        (temp_dir / "dsc.json").write_bytes(_canonical_bytes(content))

        if output_dir.exists():
            existing = sorted(p.name for p in output_dir.iterdir())
            produced = sorted(p.name for p in temp_dir.iterdir())
            if existing != produced or any(
                (output_dir / name).read_bytes() != (temp_dir / name).read_bytes()
                for name in produced
            ):
                raise ClassificationNondeterministicError(
                    f"existing {output_dir} holds different bytes for {dsc_id}"
                )
            shutil.rmtree(temp_dir)
        else:
            temp_dir.rename(output_dir)
    except BaseException:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise

    return content


def _read_sec_index(
    sec_dir: Path, relevant_names: set[str]
) -> tuple[list[dict[str, Any]], dict[str, set[str]], dict[str, str]]:
    if not sec_dir.is_dir():
        raise ClassificationInputError(f"missing SEC index directory {sec_dir}")
    events: list[dict[str, Any]] = []
    name_to_ciks: dict[str, set[str]] = {}
    files_sha256: dict[str, str] = {}
    norm_cache: dict[str, str] = {}
    master_files = sorted(
        path
        for path in sec_dir.iterdir()
        if path.is_file() and _MASTER_RE.match(path.name)
    )
    for path in master_files:
        files_sha256[path.name] = _file_sha256(path)
        for cik, company, form, date_text, filename in _iter_master_rows(path):
            normalized = norm_cache.get(company)
            if normalized is None:
                normalized = norm(company)
                norm_cache[company] = normalized
            if normalized in relevant_names:
                name_to_ciks.setdefault(normalized, set()).add(cik)
            base_form = form.split("/", 1)[0]
            if base_form in _REGULATORY_FORMS:
                events.append(
                    {
                        "cik": cik,
                        "company_name": company,
                        "form": form,
                        "base_form": base_form,
                        "date_filed": _as_date(date_text, "Date Filed"),
                        "filename": filename,
                        "source_index": path.name,
                    }
                )
    return (events, name_to_ciks, files_sha256)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="D-SC security classification")
    parser.add_argument("--root", required=True)
    parser.add_argument("--identity-id", required=True)
    parser.add_argument("--captures-dir", default=None)
    parser.add_argument("--sec-index-dir", default=None)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    content = classify(
        args.root,
        args.identity_id,
        captures_dir=args.captures_dir,
        sec_index_dir=args.sec_index_dir,
    )
    print(content["dsc_id"])
    print(content["dsc_hash"])
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
