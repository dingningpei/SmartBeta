#!/usr/bin/env python3
"""One-time live recorder + Gate B artifact producer for P5A-4.

This is a **recording tool, not shipped package code and not a test**: it is
never imported by ``smart_beta`` and it lives outside ``tests/`` so pytest
never collects it. It performs the one-time live work documented in the P5A-4
completion report:

1. ``probe``: one bounded live entitlement call per candidate DJIA name
   against the exact endpoint ``total_mcap`` depends on
   (``GET /tiingo/fundamentals/{ticker}/daily``), then write the frozen
   universe provenance record to ``docs/phase5a/gate_b/universe_snapshot.json``
   **before** any return/factor computation begins.
2. ``record``: read that frozen record, live-fetch the surviving universe's
   metadata / EOD prices / daily fundamentals over the Gate B window, fetch
   the real FRED ``DGS3MO`` series for the window, write all raw response
   bodies plus provenance manifests, and only then run the *same* offline
   fixture-replay path the test suite uses to produce the Gate B artifacts
   under ``docs/phase5a/gate_b/``.
3. ``offline``: skip all network calls and regenerate artifacts from the
   already-committed fixture sets.
4. ``seed-ledger``: derive the non-final resume ledger from the preserved
   blocker transcript (offline; no network).
5. ``all`` (default): ``probe`` then ``record``.

Boundedness: one authorized request is one network attempt. A Tiingo
429/transient 5xx response is never retried and never slept through; the
first transient response stops the invocation immediately and leaves the
candidate UNKNOWN. The non-final resume ledger records already-definitive
outcomes so a later resume re-probes only the still-unresolved candidates.
The only remaining retry loop is the credential-free FRED download, whose
semantics are deliberately unchanged.

Run it by hand with a real key in the environment::

    TIINGO_API_KEY=... .venv/bin/python scripts/fetch_phase5a_gate_b_fixtures.py --mode all

The Tiingo API key is read only from the environment and is never logged or
written to disk. The FRED endpoint is fully public and uses no credential.

Universe-construction freeze (see ``worker_tasks/phase5a/phase5a-plan.md``)
--------------------------------------------------------------------------
The candidate universe is the publicly documented, dated DJIA constituent
snapshot recorded in the committed ``universe_snapshot.json`` (Wikipedia's
"List of Dow Jones Industrial Average companies", revision 1369516696,
2026-08-15). Every candidate is entitlement-probed; any name whose probe does
not return HTTP 200 is excluded and named with its exact reason -- never
silently dropped and never substituted to keep the count near 30. The
surviving list is frozen in ``universe_snapshot.json`` **before** any
return/factor computation, and is held constant for the entire window.

Required terminology: this is "a fixed universe selected from a dated DJIA
constituent snapshot". It is not a historical universe, a representative
market portfolio, or a survivorship-safe index, and no index-membership
infrastructure is built.
"""

from __future__ import annotations

import argparse
import ast
import csv
import hashlib
import io
import json
import os
import re
import shutil
import sys
import time
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

import pandas as pd

# Make the repository importable when run as a plain script from anywhere.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from smart_beta.config.settings import DEFAULT_SETTINGS  # noqa: E402
from smart_beta.pipelines.capm_pilot import (  # noqa: E402
    derive_trading_dates,
    run_capm_pilot,
)
from smart_beta.pit.view import PointInTimeView  # noqa: E402
from smart_beta.research_inputs.risk_free_treasury import (  # noqa: E402
    TreasuryBillRiskFreeProvider,
)
from smart_beta.vendors.tiingo.client import (  # noqa: E402
    API_KEY_ENV_VAR,
    TiingoAPIError,
    TiingoClient,
    replay_transport,
)
from smart_beta.vendors.tiingo.source import TiingoPITSource  # noqa: E402

# ---------------------------------------------------------------------------
# Frozen Gate B parameters (chosen before any return/price fixture recording)
# ---------------------------------------------------------------------------
GATE_B_START = "2025-09-15"
GATE_B_END = "2026-09-15"
EOD_LOOKBACK_DAYS = 10

#: The single date inside the Gate B window used for every entitlement probe.
ENTITLEMENT_PROBE_DATE = "2026-06-15"

#: The publicly documented, dated DJIA constituent snapshot (see the module
#: docstring). Both the date and the list are part of the committed
#: provenance and are independently verifiable outside this repository.
SNAPSHOT_DATE = "2026-09-19"
SNAPSHOT_SOURCE = (
    "Wikipedia, 'List of Dow Jones Industrial Average companies'"
)
SNAPSHOT_SOURCE_URL = (
    "https://en.wikipedia.org/wiki/List_of_Dow_Jones_Industrial_Average_companies"
)
SNAPSHOT_REVISION_ID = 1369516696
SNAPSHOT_REVISION_TIMESTAMP = "2026-08-15T13:23:35Z"

#: The 30 public DJIA constituents as of the snapshot, in the snapshot's
#: order, with their public company names.
CANDIDATE_COMPANIES: tuple[tuple[str, str], ...] = (
    ("MMM", "3M"),
    ("GOOGL", "Alphabet (Class A)"),
    ("AXP", "American Express"),
    ("AMGN", "Amgen"),
    ("AMZN", "Amazon"),
    ("AAPL", "Apple"),
    ("BA", "Boeing"),
    ("CAT", "Caterpillar"),
    ("CVX", "Chevron"),
    ("CSCO", "Cisco"),
    ("KO", "Coca-Cola"),
    ("DIS", "Disney"),
    ("GS", "Goldman Sachs"),
    ("HD", "Home Depot"),
    ("HON", "Honeywell Technologies"),
    ("IBM", "IBM"),
    ("JNJ", "Johnson & Johnson"),
    ("JPM", "JPMorgan Chase"),
    ("MCD", "McDonald's"),
    ("MRK", "Merck"),
    ("MSFT", "Microsoft"),
    ("NKE", "Nike"),
    ("NVDA", "Nvidia"),
    ("PG", "Procter & Gamble"),
    ("CRM", "Salesforce"),
    ("SHW", "Sherwin-Williams"),
    ("TRV", "Travelers"),
    ("UNH", "UnitedHealth Group"),
    ("V", "Visa"),
    ("WMT", "Walmart"),
)
CANDIDATE_TICKERS: tuple[str, ...] = tuple(t for t, _ in CANDIDATE_COMPANIES)

FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "tiingo" / "phase5a_gate_b"
FRED_FIXTURE_DIR = (
    REPO_ROOT / "tests" / "fixtures" / "risk_free" / "treasury_gate_b"
)
ARTIFACT_DIR = REPO_ROOT / "docs" / "phase5a" / "gate_b"
UNIVERSE_SNAPSHOT_PATH = ARTIFACT_DIR / "universe_snapshot.json"

#: Non-final staged-capture area for raw Tiingo HTTP-200 responses. It is
#: deliberately outside the canonical fixture directory so an incomplete
#: staged set can never be consumed as a Gate B fixture set.
STAGING_DIR = ARTIFACT_DIR / "staging" / "tiingo"
STAGING_LEDGER_PATH = ARTIFACT_DIR / "staging" / "staging_ledger.json"

_DAILY_PATH = "/tiingo/fundamentals/{ticker}/daily"
_EOD_PATH = "/tiingo/daily/{ticker}/prices"
_META_PATH = "/tiingo/daily/{ticker}"

UNIVERSE_TERMINOLOGY = "a fixed universe selected from a dated DJIA constituent snapshot"

#: Statuses that are transient operational blocks (never an entitlement
#: exclusion) and are NEVER retried: one authorized request is one network
#: attempt. On the first transient response the probe stops immediately and
#: the candidate is left UNKNOWN; no universe snapshot is frozen.
_TRANSIENT_STATUSES = frozenset({429, 500, 502, 503, 504})

#: A plan-tier restriction body is a genuine, permanent entitlement
#: exclusion; a 400 with this text is the only 400 treated that way.
_PLAN_TIER_MARKER = "DOW 30"

#: Non-final resume ledger: the definitive candidate outcomes gathered so
#: far plus the still-unresolved candidates. It is NEVER a frozen universe
#: and is never consumed by ``run_record``/``run_artifacts``; it exists so a
#: future resume can probe only the unresolved names instead of all 30.
PROBE_LEDGER_PATH = ARTIFACT_DIR / "entitlement_probe_ledger.json"

#: The genuine, previously-recorded blocker transcript. Its ``TICKER: STATUS``
#: lines are the only source of the already-definitive outcomes used to seed
#: the resume ledger; the stale ``universe_snapshot.json`` is never read.
BLOCKER_LOG_PATH = ARTIFACT_DIR / "evidence" / "p5a4_recorder_quota_block.log"

# FRED public CSV export (no credential required).
_SERIES_ID = "DGS3MO"
_FRED_CSV_URL = (
    "https://fred.stlouisfed.org/graph/fredgraph.csv"
    "?id={series_id}&cosd={start}&coed={end}"
)
_FRED_USER_AGENT = "smart-beta-phase5a-recorder/1.0 (+https://fred.stlouisfed.org)"


def _eod_start() -> str:
    return (
        date.fromisoformat(GATE_B_START) - timedelta(days=EOD_LOOKBACK_DAYS)
    ).isoformat()


def _write_json(path: Path, obj: object, *, indent: int = 2) -> None:
    path.write_text(json.dumps(obj, indent=indent) + "\n", encoding="utf-8")


def _body_detail(body: object) -> str:
    """Human-readable detail text from a Tiingo error body."""
    if isinstance(body, dict):
        return str(body.get("detail", body))
    return str(body)


def _classify_probe_status(status_code: int, body: object) -> str:
    """Classify exactly one probe response.

    Returns one of:

    ``accessible``           HTTP 200 -- definitive accessible.
    ``plan_tier_exclusion``  the established plan-tier HTTP 400 -- definitive
                             entitlement exclusion.
    ``transient``            429/transient 5xx -- UNKNOWN operational blocker.
    ``unexpected``           anything else -- fail closed; never an exclusion.
    """
    if status_code == 200:
        return "accessible"
    if status_code == 400 and _PLAN_TIER_MARKER in _body_detail(body):
        return "plan_tier_exclusion"
    if status_code in _TRANSIENT_STATUSES:
        return "transient"
    return "unexpected"


def _probe_once(
    client: TiingoClient, ticker: str
) -> tuple[int, object, int]:
    """Exactly ONE network attempt for one entitlement probe.

    No retry, no sleep, no quota-window waiting. Returns
    ``(status, body, n_rows)``; a raised :class:`TiingoAPIError` is returned
    as its exact status/body so the caller classifies it.
    """
    try:
        rows = client.get_fundamentals_daily(
            ticker, ENTITLEMENT_PROBE_DATE, ENTITLEMENT_PROBE_DATE
        )
        return 200, None, len(rows)
    except TiingoAPIError as exc:
        return exc.status_code, exc.body, 0


def _fetch_once(call: Callable[[], object]) -> tuple[int, object]:
    """Exactly ONE network attempt for one fixture request (no retry/sleep)."""
    try:
        return 200, call()
    except TiingoAPIError as exc:
        return exc.status_code, exc.body


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _probe_entry(
    ticker: str, company: str, status: int, body: object, n_rows: int
) -> dict:
    """One definitive entitlement-probe outcome entry."""
    entry: dict[str, object] = {
        "ticker": ticker,
        "company": company,
        "endpoint": _DAILY_PATH.format(ticker=ticker),
        "probe_date": ENTITLEMENT_PROBE_DATE,
        "status_code": status,
        "accessible": status == 200,
        "rows_returned": n_rows,
    }
    if _classify_probe_status(status, body) == "plan_tier_exclusion":
        entry["reason"] = _body_detail(body)
        entry["raw_body"] = body
    return entry


def _write_probe_ledger(
    results: dict[str, dict],
    unresolved: list[str],
    blockers: list[dict],
    *,
    source_evidence: dict | None,
) -> None:
    """Write the explicitly non-final resume ledger (never a freeze)."""
    ledger = {
        "_provenance": {
            "status": "PARTIAL_NON_FINAL",
            "gate": "B",
            "note": (
                "Non-final resume ledger. NOT a frozen universe and NOT valid "
                "provenance for any artifact. It records only already-definitive "
                "candidate outcomes so a future resume can probe the remaining "
                "unresolved candidates without re-probing resolved ones."
            ),
            "candidate_count": len(CANDIDATE_TICKERS),
            "definitive_count": len(results),
            "unresolved": unresolved,
            "source_evidence": source_evidence,
            "written_at_utc": _now_utc(),
        },
        "endpoint": _DAILY_PATH,
        "probe_date": ENTITLEMENT_PROBE_DATE,
        "definitive_results": {
            t: results[t] for t in CANDIDATE_TICKERS if t in results
        },
        "unresolved": unresolved,
        "blockers": blockers,
    }
    _write_json(PROBE_LEDGER_PATH, ledger)


def _load_probe_ledger() -> tuple[dict[str, dict], dict | None]:
    """Load only the non-final resume ledger; never the stale snapshot.

    Returns ``(definitive_results, source_evidence)``. A missing ledger is an
    empty resume. A ledger carrying a non-definitive status is refused rather
    than silently trusted.
    """
    if not PROBE_LEDGER_PATH.is_file():
        return {}, None
    ledger = json.loads(PROBE_LEDGER_PATH.read_text(encoding="utf-8"))
    provenance = ledger.get("_provenance", {})
    if provenance.get("status") != "PARTIAL_NON_FINAL":
        raise SystemExit(
            f"error: {PROBE_LEDGER_PATH} is not a PARTIAL_NON_FINAL resume "
            "ledger; refusing to consume it."
        )
    results: dict[str, dict] = {}
    for ticker, entry in ledger.get("definitive_results", {}).items():
        if ticker not in CANDIDATE_TICKERS:
            raise SystemExit(
                f"error: resume ledger names {ticker!r}, which is not a "
                "candidate in the frozen snapshot."
            )
        status = int(entry["status_code"])
        kind = _classify_probe_status(
            status, entry.get("raw_body") or entry.get("reason")
        )
        if kind not in ("accessible", "plan_tier_exclusion"):
            raise SystemExit(
                f"error: resume ledger entry for {ticker!r} is not definitive "
                f"(status {status}); refusing to trust it."
            )
        results[ticker] = entry
    return results, provenance.get("source_evidence")


def _snapshot_outcomes_are_definitive(snapshot: dict) -> bool:
    """True only for a complete, all-definitive, internally consistent freeze."""
    results = snapshot.get("entitlement_probe", {}).get("results", [])
    by_ticker = {item.get("ticker"): item for item in results}
    if set(by_ticker) != set(CANDIDATE_TICKERS):
        return False
    for item in results:
        status = int(item.get("status_code", 0))
        kind = _classify_probe_status(
            status, item.get("raw_body") or item.get("reason")
        )
        if kind not in ("accessible", "plan_tier_exclusion"):
            return False
    accessible = [
        t for t in CANDIDATE_TICKERS if int(by_ticker[t]["status_code"]) == 200
    ]
    return list(snapshot.get("frozen_universe", [])) == accessible


def _build_final_snapshot(results: dict[str, dict]) -> dict:
    """Build the one coherent final snapshot from all definitive outcomes.

    Only called when every candidate has a definitive outcome.
    """
    probes = [results[t] for t in CANDIDATE_TICKERS]
    frozen = [
        t for t in CANDIDATE_TICKERS if int(results[t]["status_code"]) == 200
    ]
    exclusions = [
        {
            "ticker": results[t]["ticker"],
            "company": results[t]["company"],
            "endpoint": results[t]["endpoint"],
            "status_code": results[t]["status_code"],
            "reason": results[t].get("reason", ""),
        }
        for t in CANDIDATE_TICKERS
        if int(results[t]["status_code"]) == 400
    ]
    return {
        "gate": "B",
        "window": {"start": GATE_B_START, "end": GATE_B_END},
        "universe_terminology": UNIVERSE_TERMINOLOGY,
        "djia_constituent_snapshot": {
            "snapshot_date": SNAPSHOT_DATE,
            "source": SNAPSHOT_SOURCE,
            "source_url": SNAPSHOT_SOURCE_URL,
            "revision_id": SNAPSHOT_REVISION_ID,
            "revision_timestamp_utc": SNAPSHOT_REVISION_TIMESTAMP,
            "retrieved_at_utc": _now_utc(),
            "candidate_count": len(CANDIDATE_TICKERS),
            "candidates": [
                {"ticker": t, "company": c} for t, c in CANDIDATE_COMPANIES
            ],
            "note": (
                "Publicly documented, dated DJIA constituent snapshot. This is "
                "NOT a historical-membership or point-in-time index feed; no "
                "index-membership infrastructure is built. The list is held "
                "constant for the entire Gate B window regardless of any "
                "real-world reconstitution."
            ),
        },
        "entitlement_probe": {
            "endpoint": _DAILY_PATH,
            "probe_date": ENTITLEMENT_PROBE_DATE,
            "method": (
                "one bounded live HTTPS GET per candidate name through "
                "TiingoClient, key read from the environment and never stored"
            ),
            "results": probes,
        },
        "exclusions": exclusions,
        "frozen_universe": frozen,
        "frozen_universe_count": len(frozen),
        "freeze_ordering": (
            "This record was written after the entitlement probes and before "
            "any return/factor computation, and the frozen_universe list is "
            "what the recorded fixtures and artifacts are generated from."
        ),
    }


def _parse_blocker_log(log_text: str) -> dict[str, dict]:
    """Transparently derive definitive outcomes from the preserved blocker
    transcript. Only ``TICKER: STATUS`` lines are read and each status is
    classified with the same rules as the live probe; unresolved candidates
    simply do not appear."""
    results: dict[str, dict] = {}
    companies = dict(CANDIDATE_COMPANIES)
    for line in log_text.splitlines():
        match = re.match(
            r"^([A-Z][A-Z0-9.]*): (\d{3})(?: \(([0-9]+) rows\))?(.*)$",
            line,
        )
        if not match:
            continue
        ticker, status_text, rows_text, tail = match.groups()
        if ticker not in companies or ticker in results:
            continue
        status = int(status_text)
        body: object = None
        if tail.strip():
            try:
                body = ast.literal_eval(tail.strip())
            except (ValueError, SyntaxError):
                body = tail.strip()
        if _classify_probe_status(status, body) not in (
            "accessible",
            "plan_tier_exclusion",
        ):
            continue
        n_rows = int(rows_text) if rows_text else 0
        results[ticker] = _probe_entry(
            ticker, companies[ticker], status, body, n_rows
        )
    return results


def run_seed_ledger() -> int:
    """Seed the non-final resume ledger from the preserved blocker log."""
    if not BLOCKER_LOG_PATH.is_file():
        print(
            f"error: blocker log {BLOCKER_LOG_PATH} not found; cannot seed "
            "the resume ledger.",
            file=sys.stderr,
        )
        return 2
    log_text = BLOCKER_LOG_PATH.read_text(encoding="utf-8")
    results = _parse_blocker_log(log_text)
    unresolved = [t for t, _ in CANDIDATE_COMPANIES if t not in results]
    source_evidence = {
        "path": str(BLOCKER_LOG_PATH.relative_to(REPO_ROOT)),
        "sha256": hashlib.sha256(log_text.encode("utf-8")).hexdigest(),
        "derivation": (
            "Definitive outcomes re-read from the preserved blocker log's "
            "'TICKER: STATUS' lines; no outcome was invented or copied from "
            "the stale universe_snapshot.json."
        ),
    }
    _write_probe_ledger(results, unresolved, [], source_evidence=source_evidence)
    print(
        f"wrote non-final resume ledger {PROBE_LEDGER_PATH} "
        f"({len(results)} definitive, {len(unresolved)} unresolved: "
        f"{unresolved})"
    )
    return 0


# ---------------------------------------------------------------------------
# Mode 1: entitlement probe + universe freeze
# ---------------------------------------------------------------------------
def run_probe(client: TiingoClient | None = None) -> int:
    if client is None:
        api_key = os.environ.get(API_KEY_ENV_VAR)
        if not api_key:
            print(
                f"error: {API_KEY_ENV_VAR} is not set. This recorder needs a "
                "real Tiingo key; it never logs or writes the key itself.",
                file=sys.stderr,
            )
            return 2
        client = TiingoClient(api_key=api_key)

    # Resume only from the non-final ledger. The stale
    # ``universe_snapshot.json`` is NEVER read as input.
    prior, source_evidence = _load_probe_ledger()
    results: dict[str, dict] = dict(prior)
    blockers: list[dict] = []

    for ticker, company in CANDIDATE_COMPANIES:
        path = _DAILY_PATH.format(ticker=ticker)
        if ticker in results:
            entry = results[ticker]
            status = int(entry["status_code"])
            n_rows = int(entry.get("rows_returned") or 0)
            print(
                f"{ticker}: {status} ({n_rows} rows) [resumed]"
                if status == 200
                else f"{ticker}: {status} {entry.get('reason')} [resumed]",
                flush=True,
            )
            continue

        status, body, n_rows = _probe_once(client, ticker)
        kind = _classify_probe_status(status, body)
        if kind in ("accessible", "plan_tier_exclusion"):
            entry = _probe_entry(ticker, company, status, body, n_rows)
            results[ticker] = entry
            print(
                f"{ticker}: {status} ({n_rows} rows)"
                if kind == "accessible"
                else f"{ticker}: {status} {entry.get('reason')}",
                flush=True,
            )
            continue

        # First transient or unexpected response: stop immediately. Never
        # continue probing, never freeze a snapshot, never reinterpret the
        # response as an entitlement exclusion.
        blockers.append(
            {
                "ticker": ticker,
                "company": company,
                "endpoint": path,
                "status_code": status,
                "classification": kind,
                "body": body,
            }
        )
        print(
            f"BLOCKED: {ticker} probe returned {kind} status {status}: "
            f"{body!r}. Stopping immediately; no universe snapshot frozen.",
            file=sys.stderr,
        )
        break

    unresolved = [t for t, _ in CANDIDATE_COMPANIES if t not in results]
    if unresolved:
        _write_probe_ledger(
            results, unresolved, blockers, source_evidence=source_evidence
        )
        print(
            f"wrote non-final resume ledger to {PROBE_LEDGER_PATH} "
            f"({len(results)}/{len(CANDIDATE_TICKERS)} definitive, "
            f"{len(unresolved)} unresolved)",
            file=sys.stderr,
        )
        return 3

    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    # One coherent, atomic freeze: build the complete 30-candidate snapshot
    # and replace the file in a single rename, so a half-written snapshot can
    # never be read. The non-final resume ledger is then removed.
    snapshot_tmp = UNIVERSE_SNAPSHOT_PATH.with_name(
        UNIVERSE_SNAPSHOT_PATH.name + ".tmp"
    )
    _write_json(snapshot_tmp, _build_final_snapshot(results))
    os.replace(snapshot_tmp, UNIVERSE_SNAPSHOT_PATH)
    PROBE_LEDGER_PATH.unlink(missing_ok=True)
    frozen_count = sum(
        1 for t in CANDIDATE_TICKERS if results[t]["status_code"] == 200
    )
    print(
        f"wrote {UNIVERSE_SNAPSHOT_PATH} "
        f"({frozen_count}/{len(CANDIDATE_TICKERS)} accessible)"
    )
    if frozen_count < 15:
        print(
            "WARNING: fewer than 15 names survived entitlement; the frozen "
            "Gate B rule's ~30-name target may be unworkable -- report this "
            "as a design barrier rather than substituting a new rule.",
            file=sys.stderr,
        )
    return 0


# ---------------------------------------------------------------------------
# Mode 2: live fixture recording (survivors + FRED)
# ---------------------------------------------------------------------------
def _download_fred(
    start: str, end: str, *, attempts: int = 8, delay: float = 20.0
) -> tuple[str, str]:
    """Fetch the public FRED CSV export, retrying transient network errors."""
    url = _FRED_CSV_URL.format(series_id=_SERIES_ID, start=start, end=end)
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            request = urllib.request.Request(
                url, headers={"User-Agent": _FRED_USER_AGENT}
            )
            with urllib.request.urlopen(request, timeout=60) as response:
                if response.status != 200:
                    raise RuntimeError(
                        f"unexpected HTTP status {response.status} for {url}"
                    )
                raw = response.read()
            text = raw.decode("utf-8")
            header = text.splitlines()[0] if text else ""
            if header.strip() != f"observation_date,{_SERIES_ID}":
                raise RuntimeError(
                    f"unexpected FRED CSV header {header!r}; refusing to "
                    "record. Report a named finding instead of silently "
                    "adapting."
                )
            return url, text
        except Exception as exc:  # noqa: BLE001 - retry any transient failure
            last_error = exc
            print(
                f"  FRED fetch attempt {attempt}/{attempts} failed: {exc!r}",
                flush=True,
            )
            if attempt < attempts:
                time.sleep(delay)
    raise RuntimeError(
        f"FRED fetch failed after {attempts} attempts: {last_error!r}"
    )


def _parse_fred_observations(raw_csv: str) -> list[dict[str, object]]:
    rows = csv.DictReader(io.StringIO(raw_csv))
    observations: list[dict[str, object]] = []
    for row in rows:
        raw_value = (row.get(_SERIES_ID) or "").strip()
        observations.append(
            {
                "observation_date": row["observation_date"],
                _SERIES_ID: None if raw_value == "" else float(raw_value),
            }
        )
    return observations


def _load_frozen_universe() -> tuple[list[str], dict]:
    if not UNIVERSE_SNAPSHOT_PATH.is_file():
        raise SystemExit(
            f"error: {UNIVERSE_SNAPSHOT_PATH} does not exist. Run "
            "`--mode probe` first so the universe is frozen before any "
            "return/factor computation."
        )
    snapshot = json.loads(UNIVERSE_SNAPSHOT_PATH.read_text(encoding="utf-8"))
    if not _snapshot_outcomes_are_definitive(snapshot):
        raise SystemExit(
            "error: the universe snapshot does not contain a complete, "
            "definitive probe outcome for every candidate (a transient/unknown "
            "or missing result is present); refusing to use it as provenance. "
            "Re-run `--mode probe` once entitlement access is restored."
        )
    frozen = list(snapshot["frozen_universe"])
    recorded_candidates = [
        item["ticker"] for item in snapshot["djia_constituent_snapshot"]["candidates"]
    ]
    if recorded_candidates != list(CANDIDATE_TICKERS):
        raise SystemExit(
            "error: the committed universe snapshot's candidate list does not "
            "match this recorder's frozen DJIA snapshot; refusing to proceed."
        )
    return frozen, snapshot


def _expected_tiingo_filenames(frozen: list[str], eod_start: str) -> list[str]:
    names: list[str] = []
    for ticker in frozen:
        low = ticker.lower()
        names.extend(
            [
                f"{low}_meta.json",
                f"{low}_eod_prices_{eod_start}_{GATE_B_END}.json",
                f"{low}_fundamentals_daily_{GATE_B_START}_{GATE_B_END}.json",
            ]
        )
    return names


def _load_complete_tiingo_recordings(
    frozen: list[str], eod_start: str
) -> tuple[dict[str, dict[str, object]], dict[str, object]] | None:
    """Reuse a previously written, complete Tiingo fixture set (resume)."""
    manifest_path = FIXTURE_DIR / "manifest.json"
    if not manifest_path.is_file():
        return None
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    recordings = manifest.get("recordings", {})
    expected = set(_expected_tiingo_filenames(frozen, eod_start))
    if set(recordings) != expected:
        return None
    if any(int(entry["status_code"]) != 200 for entry in recordings.values()):
        return None
    if not all((FIXTURE_DIR / name).is_file() for name in expected):
        return None
    bodies = {
        name: json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))
        for name in expected
    }
    return recordings, bodies


def _capture_specs(frozen: list[str]) -> list[dict]:
    """Deterministic full expected Tiingo request-identity set.

    For each frozen ticker: metadata, EOD prices (with the frozen EOD
    lookback start), and daily fundamentals over the frozen Gate B window.
    The identity is the canonical fixture filename each capture promotes to.
    """
    eod_start = _eod_start()
    specs: list[dict] = []
    for ticker in frozen:
        low = ticker.lower()
        specs.append(
            {
                "identity": f"{low}_meta.json",
                "kind": "meta",
                "ticker": ticker,
                "endpoint": _META_PATH.format(ticker=ticker),
                "params": {},
            }
        )
        specs.append(
            {
                "identity": f"{low}_eod_prices_{eod_start}_{GATE_B_END}.json",
                "kind": "eod",
                "ticker": ticker,
                "endpoint": _EOD_PATH.format(ticker=ticker),
                "params": {"startDate": eod_start, "endDate": GATE_B_END},
            }
        )
        specs.append(
            {
                "identity": (
                    f"{low}_fundamentals_daily_"
                    f"{GATE_B_START}_{GATE_B_END}.json"
                ),
                "kind": "fundamentals",
                "ticker": ticker,
                "endpoint": _DAILY_PATH.format(ticker=ticker),
                "params": {
                    "startDate": GATE_B_START,
                    "endDate": GATE_B_END,
                },
            }
        )
    return specs


def _canonical_body_json(body: object) -> str:
    """Canonical raw representation of a Tiingo JSON body for hashing."""
    return json.dumps(
        body, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )


def _content_sha256(body: object) -> str:
    return hashlib.sha256(
        _canonical_body_json(body).encode("utf-8")
    ).hexdigest()


def _call_for_spec(client: TiingoClient, spec: dict) -> Callable[[], object]:
    kind = spec["kind"]
    ticker = spec["ticker"]
    params = spec["params"]
    if kind == "meta":
        return lambda: client.get_meta(ticker)
    if kind == "eod":
        return lambda: client.get_eod_prices(
            ticker, params["startDate"], params["endDate"]
        )
    if kind == "fundamentals":
        return lambda: client.get_fundamentals_daily(
            ticker, params["startDate"], params["endDate"]
        )
    raise AssertionError(f"unknown capture kind {kind!r}")


def _staged_path(identity: str) -> Path:
    return STAGING_DIR / identity


def _write_staged_capture(spec: dict, body: object) -> dict:
    """Persist exactly one HTTP-200 response into the non-final staging area."""
    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    record = {
        "identity": spec["identity"],
        "ticker": spec["ticker"],
        "endpoint": spec["endpoint"],
        "params": dict(spec["params"]),
        "retrieved_at_utc": _now_utc(),
        "status_code": 200,
        "body_format": "tiingo-native-json-canonical",
        "content_sha256": _content_sha256(body),
        "body": body,
    }
    _write_json(_staged_path(spec["identity"]), record)
    return record


def _validate_staged_record(record: dict, spec: dict, path: Path) -> None:
    """Fail closed on any identity/metadata/hash inconsistency."""
    if record.get("identity") != spec["identity"]:
        raise SystemExit(
            f"error: staged capture {path.name} identity mismatch "
            f"({record.get('identity')!r} != {spec['identity']!r})."
        )
    if record.get("ticker") != spec["ticker"]:
        raise SystemExit(
            f"error: staged capture {path.name} ticker mismatch."
        )
    if record.get("endpoint") != spec["endpoint"]:
        raise SystemExit(
            f"error: staged capture {path.name} endpoint mismatch."
        )
    if dict(record.get("params", {})) != dict(spec["params"]):
        raise SystemExit(
            f"error: staged capture {path.name} request-parameter mismatch."
        )
    if not record.get("retrieved_at_utc"):
        raise SystemExit(
            f"error: staged capture {path.name} lacks a retrieval timestamp."
        )
    if int(record.get("status_code", 0)) != 200:
        raise SystemExit(
            f"error: staged capture {path.name} is not an HTTP 200 success."
        )
    actual = _content_sha256(record.get("body"))
    if actual != record.get("content_sha256"):
        raise SystemExit(
            f"error: staged capture {path.name} content hash mismatch "
            f"({actual} != {record.get('content_sha256')}); failing closed."
        )


def _load_staged_captures(specs: list[dict]) -> dict[str, dict]:
    """Validate staged captures already on disk; return {identity: record}.

    Any unexpected staged file, duplicate identity, metadata conflict, or
    hash mismatch fails closed. No network request is made here.
    """
    expected = {spec["identity"]: spec for spec in specs}
    if len(expected) != len(specs):
        raise SystemExit("error: duplicate request identity in expected set.")
    valid: dict[str, dict] = {}
    if not STAGING_DIR.is_dir():
        return valid
    for path in sorted(STAGING_DIR.iterdir()):
        if not path.is_file():
            continue
        if path.name not in expected:
            raise SystemExit(
                f"error: unexpected staged capture {path.name!r}; "
                "failing closed."
            )
        record = json.loads(path.read_text(encoding="utf-8"))
        _validate_staged_record(record, expected[path.name], path)
        valid[path.name] = record
    return valid


def _write_staging_ledger(
    specs: list[dict],
    valid: dict[str, dict],
    *,
    state: str,
    blocker: dict | None,
) -> None:
    """Write the explicitly non-final staging ledger (never a canonical set)."""
    ledger = {
        "_provenance": {
            "status": "NON_FINAL_STAGING",
            "gate": "B",
            "note": (
                "Non-final staged Tiingo capture state. NOT certification "
                "evidence and NOT the canonical Gate B fixture set; it may "
                "only be promoted through the validated atomic promotion path."
            ),
            "candidate_universe": list(CANDIDATE_TICKERS),
            "frozen_universe": list(
                dict.fromkeys(spec["ticker"] for spec in specs)
            ),
            "gate_b_window": {"start": GATE_B_START, "end": GATE_B_END},
            "eod_lookback_start": _eod_start(),
            "expected_identity_count": len(specs),
            "valid_captured_count": len(valid),
            "state": state,
            "updated_at_utc": _now_utc(),
        },
        "expected_identities": [
            {
                "identity": spec["identity"],
                "ticker": spec["ticker"],
                "endpoint": spec["endpoint"],
                "params": dict(spec["params"]),
            }
            for spec in specs
        ],
        "valid_captured_identities": [
            spec["identity"] for spec in specs if spec["identity"] in valid
        ],
        "remaining_identities": [
            spec["identity"] for spec in specs if spec["identity"] not in valid
        ],
        "captures": {
            spec["identity"]: {
                "ticker": valid[spec["identity"]]["ticker"],
                "endpoint": valid[spec["identity"]]["endpoint"],
                "params": dict(valid[spec["identity"]]["params"]),
                "retrieved_at_utc": valid[spec["identity"]][
                    "retrieved_at_utc"
                ],
                "status_code": valid[spec["identity"]]["status_code"],
                "content_sha256": valid[spec["identity"]]["content_sha256"],
            }
            for spec in specs
            if spec["identity"] in valid
        },
        "last_blocker": blocker,
    }
    STAGING_LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    _write_json(STAGING_LEDGER_PATH, ledger)


def _run_staged_capture(client: TiingoClient, specs: list[dict]) -> int:
    """Capture only genuinely missing identities; stop on first non-200.

    One authorized request is exactly one network attempt: no retry, no
    sleep, no quota-window waiting. Prior valid staged successes survive.
    """
    valid = _load_staged_captures(specs)
    _write_staging_ledger(specs, valid, state="INCOMPLETE", blocker=None)
    for spec in specs:
        identity = spec["identity"]
        if identity in valid:
            continue
        status, body = _fetch_once(_call_for_spec(client, spec))
        print(f"stage {spec['endpoint']} -> {status}", flush=True)
        if status == 200:
            valid[identity] = _write_staged_capture(spec, body)
            _write_staging_ledger(
                specs, valid, state="INCOMPLETE", blocker=None
            )
            continue
        blocker = {
            "identity": identity,
            "ticker": spec["ticker"],
            "endpoint": spec["endpoint"],
            "params": dict(spec["params"]),
            "status_code": status,
            "is_transient": status in _TRANSIENT_STATUSES,
            "body": body,
        }
        _write_staging_ledger(
            specs, valid, state="INCOMPLETE", blocker=blocker
        )
        print(
            f"BLOCKED: fixture request {spec['endpoint']} returned HTTP "
            f"{status} {body!r}. Stopping immediately; prior staged "
            "successes preserved and no canonical fixtures written.",
            file=sys.stderr,
        )
        return 3
    _write_staging_ledger(
        specs, valid, state="COMPLETE_NOT_PROMOTED", blocker=None
    )
    return 0


def _validate_complete_staged(
    specs: list[dict],
    valid: dict[str, dict],
    frozen: list[str],
    snapshot: dict,
) -> None:
    """Promotion precondition: the exact complete, consistent 200 set."""
    expected_ids = [spec["identity"] for spec in specs]
    if len(expected_ids) != len(set(expected_ids)):
        raise SystemExit("error: duplicate expected request identity.")
    if set(valid) != set(expected_ids):
        missing = sorted(set(expected_ids) - set(valid))
        unexpected = sorted(set(valid) - set(expected_ids))
        raise SystemExit(
            "error: staged set is not the exact expected identity set; "
            f"missing={missing}, unexpected={unexpected}."
        )
    if list(dict.fromkeys(spec["ticker"] for spec in specs)) != list(frozen):
        raise SystemExit(
            "error: staged request tickers do not match the frozen universe."
        )
    if list(snapshot.get("frozen_universe", [])) != list(frozen):
        raise SystemExit(
            "error: snapshot frozen universe does not match the staged set."
        )
    for spec in specs:
        record = valid[spec["identity"]]
        _validate_staged_record(record, spec, _staged_path(spec["identity"]))


def _build_tiingo_manifest(
    specs: list[dict],
    valid: dict[str, dict],
    frozen: list[str],
    snapshot: dict,
) -> dict:
    eod_start = _eod_start()
    recordings: dict[str, dict] = {}
    for spec in specs:
        entry: dict[str, object] = {
            "url_path": spec["endpoint"],
            "params": dict(spec["params"]),
            "status_code": 200,
        }
        if spec["kind"] == "fundamentals":
            entry["plan_tier_limited"] = False
        recordings[spec["identity"]] = entry
    return {
        "_provenance": {
            "live_recorded": True,
            "recorded_at": date.today().isoformat(),
            "retrieved_at_utc": _now_utc(),
            "source": "Tiingo",
            "retrieval_method": (
                "Live HTTPS GETs through TiingoClient with the key read "
                f"from the {API_KEY_ENV_VAR} environment variable; the key "
                "is never logged or written"
            ),
            "api_key_stored": False,
            "gate_b_window": {"start": GATE_B_START, "end": GATE_B_END},
            "candidate_universe": list(CANDIDATE_TICKERS),
            "frozen_universe": list(frozen),
            "excluded_names": [
                {
                    "ticker": exc["ticker"],
                    "status_code": exc["status_code"],
                    "reason": exc["reason"],
                }
                for exc in snapshot["exclusions"]
            ],
            "eod_lookback_start": eod_start,
            "gate_b_disposition": "RUN",
            "note": (
                "Raw Tiingo response bodies (verbatim, no wrapper) for "
                "the frozen surviving universe only. Promoted atomically "
                "from a complete, hash-validated staged capture set."
            ),
        },
        "recordings": recordings,
    }


def _promote_staged_to_canonical(
    specs: list[dict],
    valid: dict[str, dict],
    frozen: list[str],
    snapshot: dict,
) -> None:
    """Atomically promote a complete validated staged set to canonical.

    Canonical consumers only ever see the previous valid set or the new
    complete set: bodies + manifest are built in a sibling temp directory
    and swapped in with renames. Any failure leaves the previous set intact
    (or canonical absent).
    """
    _validate_complete_staged(specs, valid, frozen, snapshot)

    parent = FIXTURE_DIR.parent
    parent.mkdir(parents=True, exist_ok=True)
    tmp_dir = parent / (FIXTURE_DIR.name + ".promote-tmp")
    backup_dir = parent / (FIXTURE_DIR.name + ".promote-backup")
    shutil.rmtree(tmp_dir, ignore_errors=True)
    shutil.rmtree(backup_dir, ignore_errors=True)
    tmp_dir.mkdir(parents=True)
    try:
        for spec in specs:
            _write_json(
                tmp_dir / spec["identity"], valid[spec["identity"]]["body"]
            )
        _write_json(
            tmp_dir / "manifest.json",
            _build_tiingo_manifest(specs, valid, frozen, snapshot),
        )
    except BaseException:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise

    had_previous = FIXTURE_DIR.exists()
    if had_previous:
        os.rename(FIXTURE_DIR, backup_dir)
    try:
        os.rename(tmp_dir, FIXTURE_DIR)
    except BaseException:
        if had_previous and backup_dir.exists():
            os.rename(backup_dir, FIXTURE_DIR)
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise
    shutil.rmtree(backup_dir, ignore_errors=True)


def _fred_fixture_complete() -> bool:
    manifest_path = FRED_FIXTURE_DIR / "manifest.json"
    body_path = FRED_FIXTURE_DIR / f"dgs3mo_{GATE_B_START}_{GATE_B_END}.json"
    return manifest_path.is_file() and body_path.is_file()


def _write_fred_fixture(raw_csv: str, url: str, retrieved_at: str) -> None:
    body_filename = f"dgs3mo_{GATE_B_START}_{GATE_B_END}.json"
    body = {
        "_comment": (
            "P5A-4 (Gate B) fixture body. 'raw_csv' is the verbatim FRED "
            "DGS3MO CSV export for the Gate B window; 'observations' is the "
            "same data parsed to JSON, with holiday values as null."
        ),
        "raw_csv": raw_csv,
        "observations": _parse_fred_observations(raw_csv),
    }
    _write_json(FRED_FIXTURE_DIR / body_filename, body)
    digest = hashlib.sha256(raw_csv.encode("utf-8")).hexdigest()
    manifest = {
        "_provenance": {
            "live_recorded": True,
            "recorded_at": date.today().isoformat(),
            "retrieved_at_utc": retrieved_at,
            "source": "Federal Reserve Bank of St. Louis (FRED)",
            "series_id": _SERIES_ID,
            "series_name": (
                "Market Yield on U.S. Treasury Securities at 3-Month Constant "
                "Maturity, Quoted on an Investment Basis"
            ),
            "units": "percent per annum, investment basis, not seasonally adjusted",
            "retrieval_method": (
                "HTTPS GET of FRED's public fredgraph.csv export endpoint; no "
                "API key or credential used or stored"
            ),
            "url": url,
            "date_range": {"start": GATE_B_START, "end": GATE_B_END},
            "publication_calendar": (
                "Treasury business days only; FRED emits a blank value on "
                "market holidays (the date row is retained, the value is empty)"
            ),
            "note": (
                "Gate B offline replay source; the DGS3MO series is never "
                "fetched during pytest."
            ),
        },
        "recordings": {
            body_filename: {
                "url": url,
                "status_code": 200,
                "raw_csv_sha256": digest,
                "n_date_rows": len(raw_csv.splitlines()) - 1,
            }
        },
    }
    _write_json(FRED_FIXTURE_DIR / "manifest.json", manifest)


def run_fred() -> int:
    """Fetch/reuse the Gate B FRED DGS3MO fixture only."""
    FRED_FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    if _fred_fixture_complete():
        print("reusing committed FRED fixture")
        return 0
    retrieved_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    fred_url, raw_csv = _download_fred(GATE_B_START, GATE_B_END)
    _write_fred_fixture(raw_csv, fred_url, retrieved_at)
    print(f"wrote FRED fixture + manifest.json to {FRED_FIXTURE_DIR}")
    return 0


def run_stage(client: TiingoClient | None = None) -> int:
    """Bounded staged Tiingo capture + atomic canonical promotion ONLY.

    This is the staged fixture-recording path by itself: it never touches
    FRED and never runs the artifact pipeline.
    """
    frozen, snapshot = _load_frozen_universe()
    existing = _load_complete_tiingo_recordings(frozen, _eod_start())
    if existing is not None:
        recordings, _bodies = existing
        print(
            f"reusing complete Tiingo fixture set "
            f"({len(recordings)} recordings)"
        )
        return 0
    if client is None:
        api_key = os.environ.get(API_KEY_ENV_VAR)
        if not api_key:
            print(
                f"error: {API_KEY_ENV_VAR} is not set. This recorder needs a "
                "real Tiingo key; it never logs or writes the key itself.",
                file=sys.stderr,
            )
            return 2
        client = TiingoClient(api_key=api_key)

    # Bounded, resumable staged capture: one attempt per genuinely missing
    # identity, stop on the first transient/unexpected response, and no
    # canonical fixture file until the complete expected set has validated
    # HTTP-200 captures.
    specs = _capture_specs(frozen)
    code = _run_staged_capture(client, specs)
    if code != 0:
        return code
    valid = _load_staged_captures(specs)
    _promote_staged_to_canonical(specs, valid, frozen, snapshot)
    print(
        f"promoted {len(specs)} Tiingo recordings + manifest.json to "
        f"{FIXTURE_DIR}"
    )
    return 0


def run_record(client: TiingoClient | None = None) -> int:
    code = run_stage(client)
    if code != 0:
        return code
    FRED_FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    if _fred_fixture_complete():
        print("reusing committed FRED fixture")
    else:
        retrieved_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        fred_url, raw_csv = _download_fred(GATE_B_START, GATE_B_END)
        _write_fred_fixture(raw_csv, fred_url, retrieved_at)
        print(f"wrote FRED fixture + manifest.json to {FRED_FIXTURE_DIR}")
    return 0


# ---------------------------------------------------------------------------
# Mode 3: offline replay + Gate B artifact production
# ---------------------------------------------------------------------------
def _load_tiingo_recordings() -> dict[str, tuple[int, object]]:
    manifest = json.loads(
        (FIXTURE_DIR / "manifest.json").read_text(encoding="utf-8")
    )
    path_recordings: dict[str, tuple[int, object]] = {}
    for filename, entry in manifest["recordings"].items():
        body = json.loads((FIXTURE_DIR / filename).read_text(encoding="utf-8"))
        path_recordings[str(entry["url_path"])] = (
            int(entry["status_code"]),
            body,
        )
    return path_recordings


def _load_fred_frame() -> pd.DataFrame:
    filename = f"dgs3mo_{GATE_B_START}_{GATE_B_END}.json"
    body = json.loads(
        (FRED_FIXTURE_DIR / filename).read_text(encoding="utf-8")
    )
    return pd.read_csv(io.StringIO(body["raw_csv"]))


def run_artifacts() -> int:
    frozen, snapshot = _load_frozen_universe()
    if not (FIXTURE_DIR / "manifest.json").is_file():
        raise SystemExit(
            "error: no committed Gate B Tiingo fixture manifest; run "
            "`--mode record` first."
        )
    committed_manifest = json.loads(
        (FIXTURE_DIR / "manifest.json").read_text(encoding="utf-8")
    )
    if (committed_manifest.get("_provenance", {}).get("gate_b_disposition")
            == "BLOCKED"):
        raise SystemExit(
            "error: the committed manifest records a BLOCKED recording; "
            "refusing to generate artifacts from an invalid fixture set."
        )

    offline_client = TiingoClient(
        transport=replay_transport(_load_tiingo_recordings())
    )
    fred_frame = _load_fred_frame()

    source = TiingoPITSource(list(frozen), client=offline_client)
    view = PointInTimeView(source)
    trading_dates = derive_trading_dates(view, GATE_B_START, GATE_B_END)
    risk_free = TreasuryBillRiskFreeProvider(
        fred_frame, trading_dates=trading_dates
    )

    # Gate B keeps DEFAULT_SETTINGS unmodified: its larger cross-section is
    # exactly what the trusted bottom-market-cap screen was designed for.
    result = run_capm_pilot(
        frozen,
        GATE_B_START,
        GATE_B_END,
        tiingo_client=offline_client,
        risk_free=risk_free,
        settings=DEFAULT_SETTINGS,
    )

    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    result.factor.to_csv(
        ARTIFACT_DIR / "artifact_a_market_factor.csv", index=False
    )
    _write_json(
        ARTIFACT_DIR / "artifact_a_market_factor.json",
        json.loads(
            result.factor.to_json(
                orient="records",
                date_format="iso",
                double_precision=15,
            )
        ),
    )
    result.diagnostics.to_csv(
        ARTIFACT_DIR / "artifact_b_constituent_diagnostics.csv", index=False
    )
    _write_json(
        ARTIFACT_DIR / "artifact_b_constituent_diagnostics.json",
        json.loads(
            result.diagnostics.to_json(
                orient="records",
                date_format="iso",
                double_precision=15,
            )
        ),
    )
    result.risk_free_diagnostics.to_csv(
        ARTIFACT_DIR / "artifact_b_risk_free_diagnostics.csv", index=False
    )
    _write_json(
        ARTIFACT_DIR / "artifact_b_risk_free_diagnostics.json",
        json.loads(
            result.risk_free_diagnostics.to_json(
                orient="records",
                date_format="iso",
                double_precision=15,
            )
        ),
    )
    _write_json(
        ARTIFACT_DIR / "artifact_c_statistical_summary.json",
        result.statistics,
    )

    disposition = {
        "gate": "B",
        "disposition": "RUN",
        "gate_b_pass": True,
        "gate_b_claim": "BOUNDED FIXED-UNIVERSE SCALABILITY",
        "universe_terminology": UNIVERSE_TERMINOLOGY,
        "window": {"start": GATE_B_START, "end": GATE_B_END},
        "candidate_universe": list(CANDIDATE_TICKERS),
        "candidate_universe_count": len(CANDIDATE_TICKERS),
        "frozen_universe": list(frozen),
        "frozen_universe_count": len(frozen),
        "excluded_names": snapshot["exclusions"],
        "settings": "DEFAULT_SETTINGS (unmodified)",
        "artifacts": {"A": "RUN", "B": "RUN", "C": "RUN"},
        "trading_date_count": int(len(result.trading_dates)),
        "non_nan_mkt_observations": int(result.factor["MKT"].notna().sum()),
        "statistics": result.statistics,
        "not_demonstrated": (
            "representative US market coverage, survivorship-safe historical "
            "membership, publication-grade CAPM replication, investment "
            "performance"
        ),
        "next_task": {"p5a_5": "NOT STARTED"},
    }
    _write_json(ARTIFACT_DIR / "GATE_B_DISPOSITION.json", disposition)

    print(
        f"wrote Gate B artifacts to {ARTIFACT_DIR} "
        f"(trading_dates={len(result.trading_dates)}, "
        f"non_nan_MKT={int(result.factor['MKT'].notna().sum())})"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        choices=(
            "probe",
            "stage",
            "record",
            "fred",
            "offline",
            "all",
            "seed-ledger",
        ),
        default="all",
        help="which phase(s) to run (default: all)",
    )
    args = parser.parse_args()

    if args.mode == "seed-ledger":
        return run_seed_ledger()
    if args.mode == "stage":
        return run_stage()
    if args.mode in ("probe", "all"):
        code = run_probe()
        if code != 0:
            return code
    if args.mode in ("record", "all"):
        code = run_record()
        if code != 0:
            return code
    if args.mode == "fred":
        return run_fred()
    if args.mode in ("record", "offline", "all"):
        code = run_artifacts()
        if code != 0:
            return code
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
