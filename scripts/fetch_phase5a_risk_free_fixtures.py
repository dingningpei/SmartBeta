#!/usr/bin/env python3
"""One-time live recorder for P5A-1's raw FRED ``DGS3MO`` fixture.

This is a **recording tool, not shipped package code and not a test**: it is
never imported by ``smart_beta`` and it lives outside ``tests/`` so pytest
never collects it. It performs the live download documented in the P5A-1
completion report and writes the raw FRED CSV export, plus a provenance
manifest, to ``tests/fixtures/risk_free/treasury/``.

Unlike the Tiingo/Tushare recorders this series is fully public: **no API key
or credential of any kind is required**, and none is read, logged, or written.
FRED publishes a plain CSV export at::

    https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS3MO

with optional ``cosd``/``coed`` inclusive date bounds. That endpoint is what
this script calls.

The repository's ``.gitignore`` deliberately excludes ``*.csv`` ("Real vendor
data must never be committed"), and every existing fixture body is JSON, so
the raw CSV bytes are stored **verbatim as a JSON string** (``raw_csv``) with
the parsed ``observations`` alongside, rather than as an ignored ``.csv``
file. The raw bytes remain exactly recoverable and are what the test suite
replays.

Run it once, by hand, from the repository root::

    .venv/bin/python scripts/fetch_phase5a_risk_free_fixtures.py \
        --start 2025-09-01 --end 2026-09-16

The resulting fixture is the offline replay source for
``tests/test_risk_free_treasury.py``. Re-running the script overwrites the
body and manifest; the committed copies are the frozen Gate A evidence.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import sys
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_OUT_DIR = REPO_ROOT / "tests" / "fixtures" / "risk_free" / "treasury"

# Conservative superset of Gate A's eventual ~3-month window: P5A-2's exact
# dates are not frozen yet, so this records the full trailing year available
# at implementation time (DGS3MO's most recent published observation).
DEFAULT_START = "2025-09-01"
DEFAULT_END = "2026-09-16"

SERIES_ID = "DGS3MO"
FRED_CSV_URL = (
    "https://fred.stlouisfed.org/graph/fredgraph.csv"
    "?id={series_id}&cosd={start}&coed={end}"
)

# FRED's public graph endpoint rejects requests without a browser-like UA.
_USER_AGENT = "smart-beta-phase5a-recorder/1.0 (+https://fred.stlouisfed.org)"


def _download(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:
        if response.status != 200:
            raise RuntimeError(f"unexpected HTTP status {response.status} for {url}")
        return response.read()


def _parse_observations(raw_csv: str) -> list[dict[str, object]]:
    """Parse the raw FRED CSV into JSON-friendly observations.

    FRED represents a market holiday with an empty value on a retained date
    row; that is *not* an observation and is emitted as ``null`` here (the
    provider drops nulls at construction).
    """
    readings = csv.DictReader(io.StringIO(raw_csv))
    observations: list[dict[str, object]] = []
    for row in readings:
        raw_value = (row.get(SERIES_ID) or "").strip()
        observations.append(
            {
                "observation_date": row["observation_date"],
                SERIES_ID: None if raw_value == "" else float(raw_value),
            }
        )
    return observations


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default=DEFAULT_START, help="inclusive start date")
    parser.add_argument("--end", default=DEFAULT_END, help="inclusive end date")
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT_DIR,
        help=f"fixture output directory (default: {DEFAULT_OUT_DIR})",
    )
    args = parser.parse_args()

    url = FRED_CSV_URL.format(series_id=SERIES_ID, start=args.start, end=args.end)
    print(f"GET {url}", flush=True)
    body = _download(url)

    # Fail loudly rather than record a malformed/redacted body.
    text = body.decode("utf-8")
    header = text.splitlines()[0] if text else ""
    if header.strip() != f"observation_date,{SERIES_ID}":
        raise RuntimeError(
            f"unexpected FRED CSV header {header!r}; refusing to record. "
            "The DGS3MO published format may have changed -- report this as a "
            "named finding instead of silently adapting."
        )

    args.out.mkdir(parents=True, exist_ok=True)
    body_filename = f"dgs3mo_{args.start}_{args.end}.json"
    body_obj = {
        "_comment": (
            "P5A-1 fixture body. 'raw_csv' is the verbatim FRED DGS3MO CSV "
            "export (byte-for-byte, including holiday rows with empty "
            "values); 'observations' is the same data parsed to JSON, with "
            "holiday values as null. Stored as JSON because .gitignore "
            "excludes *.csv."
        ),
        "raw_csv": text,
        "observations": _parse_observations(text),
    }
    (args.out / body_filename).write_text(
        json.dumps(body_obj, indent=2) + "\n", encoding="utf-8"
    )
    digest = hashlib.sha256(body).hexdigest()

    retrieved_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    manifest = {
        "_provenance": {
            "live_recorded": True,
            "recorded_at": date.today().isoformat(),
            "retrieved_at_utc": retrieved_at,
            "source": "Federal Reserve Bank of St. Louis (FRED)",
            "series_id": SERIES_ID,
            "series_name": (
                "Market Yield on U.S. Treasury Securities at 3-Month Constant "
                "Maturity, Quoted on an Investment Basis"
            ),
            "units": "percent per annum, investment basis, not seasonally adjusted",
            "retrieval_method": (
                "HTTPS GET of FRED's public fredgraph.csv export endpoint; "
                "no API key or credential used or stored"
            ),
            "url": url,
            "date_range": {"start": args.start, "end": args.end},
            "publication_calendar": (
                "Treasury business days only; FRED emits a blank value on "
                "market holidays (the date row is retained, the value is empty)"
            ),
            "note": (
                "Offline replay source for tests/test_risk_free_treasury.py; "
                "the DGS3MO series itself is never fetched during pytest."
            ),
        },
        "recordings": {
            body_filename: {
                "url": url,
                "status_code": 200,
                "raw_csv_sha256": digest,
                "n_date_rows": len(text.splitlines()) - 1,
            }
        },
    }
    (args.out / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )

    print(f"wrote {body_filename} (sha256 {digest[:12]}...)")
    print(f"wrote manifest.json to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
