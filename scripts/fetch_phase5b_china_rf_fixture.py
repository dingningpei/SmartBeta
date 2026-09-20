#!/usr/bin/env python3
"""One-time live recorder for P5B-1's PBOC China risk-free-rate fixture.

This is a **recording tool, not shipped package code and not a test**: it is
never imported by ``smart_beta`` and it lives outside ``tests/`` so pytest
never collects it. It performs the live PBOC download documented in the P5B-1
completion report and writes the constructed ``(announcement_date,
effective_date, rate)`` table, plus a provenance manifest, to
``tests/fixtures/phase5b/china_rf/``.

Source (ECONOMIC-DEFINITION REPLICATION; see ``CLAUDE.md`` section 8)
---------------------------------------------------------------------
The primary source is the People's Bank of China (PBOC)'s own official
"monetary policy tools > interest-rate policy > interest-rate levels >
historical data" page, whose most recent "金融机构人民币存款基准利率"
(RMB benchmark deposit rate) table carries the **full** effective-date history
from 1990-04-15 through the last change on 2015-10-24, including the benchmark
one-year rate used by Liu-Stambaugh-Yuan (2019). This is the actual
rate-setting authority's own published record; it is **not** the literal
CSMAR/WRDS row the paper pulled, and no EXACT-SOURCE (CSMAR/WRDS) replication
is claimed.

The repository's ``.gitignore`` deliberately excludes ``*.csv`` and this is a
recording tool that stores JSON; the raw table cells are retained verbatim in
the fixture body so the parsed observations remain byte-recoverable.

Announcement vs. effective dates
--------------------------------
PBOC rate records have a public announcement (knowledge) date and an effective
date. The historical table's ``调整时间`` column is the *effective* date. The
P5B-1 bounded documentation check independently confirmed the announcement
date for the last change (announced 2015-10-23, effective 2015-10-24, one-year
deposit rate 1.50%) from an authoritative secondary source. For every other
historical change this bounded check did not separately enumerate PBOC's
announcement date, so ``announcement_date`` is set equal to ``effective_date``
as a documented conservative convention (knowledge no earlier than the
effective date, so it can never leak future information) -- this is **not** a
claim that PBOC announced on the effective date. The provider still models and
asserts the announcement/effective distinction explicitly.

Run it once, by hand, from the repository root::

    .venv/bin/python scripts/fetch_phase5b_china_rf_fixture.py

Re-running the script overwrites the body and manifest; the committed copies
are the frozen P5B-1 evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import urllib.request
from datetime import date, datetime, timezone
from html import unescape
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT_DIR = REPO_ROOT / "tests" / "fixtures" / "phase5b" / "china_rf"

#: PBOC's official "historical data" index page for interest-rate levels.
PBOC_HISTORY_INDEX_URL = (
    "https://www.pbc.gov.cn/zhengcehuobisi/125207/125213/125440/125838/125888/index.html"
)
PBOC_BASE_URL = "https://www.pbc.gov.cn"

#: The deposit-rate link text that marks the full-history benchmark-deposit
#: table. The most recent one contains the entire 1990-2015 history.
DEPOSIT_LINK_MARKER = "金融机构人民币存款基准利率"

#: Bundle one year of P5B-1's bounded-check announcement-date evidence: PBOC's
#: own historical table records the *effective* date (2015-10-24); the
#: announcement date (2015-10-23) is confirmed separately and cross-checked
#: against the People's Daily report recorded in the manifest below.
CONFIRMED_ANNOUNCEMENT_DATES = {
    "2015-10-24": "2015-10-23",
}

#: Independent secondary-source cross-check of the most recent (and
#: pilot-relevant) change. This is recorded in the manifest and verified by
#: ``tests/test_risk_free_china.py``.
CROSS_CHECK = {
    "source": "People's Daily Online / 人民网 (authoritative secondary report)",
    "url": "http://money.people.com.cn/n/2015/1023/c42877-27734190.html",
    "published": "2015-10-23",
    "claim_confirmed": (
        "PBOC lowered the one-year benchmark deposit rate by 0.25 percentage "
        "points to 1.5%, effective 2015-10-24"
    ),
    "confirms": {
        "announcement_date": "2015-10-23",
        "effective_date": "2015-10-24",
        "rate": 1.50,
    },
    "scope": (
        "Confirms only the final (2015-10-24) rate-change event and the "
        "announcement-vs-effective distinction; the full 1990-2015 history is "
        "sourced solely from PBOC's own official table."
    ),
}

_USER_AGENT = "smart-beta-phase5b-recorder/1.0 (+https://www.pbc.gov.cn)"

#: P5B-1's required first action: one bounded documentation read resolving (i)
#: PBOC's benchmark one-year deposit-rate change history and the
#: announcement-vs-effective-date distinction, and (ii) whether
#: Liu-Stambaugh-Yuan (2019) pro-rate or compound the annual rate (and,
#: bundled because it is the same source, (iii) their E/P re-formation cadence
#: and cumulative-YTD-vs-TTM earnings treatment). Performed once during P5B-1;
#: recorded here so the evidence is recoverable offline.
_BOUNDED_CHECK = {
    "performed": True,
    "references": [
        {
            "source": (
                "Liu, Stambaugh, Yuan (2019), 'Size and Value in China', "
                "Journal of Financial Economics 134(1), 48-69"
            ),
            "url": "https://www.nber.org/system/files/working_papers/w24458/w24458.pdf",
        },
        {
            "source": (
                "Liu, Stambaugh, Yuan, Online Appendix for 'Size and Value in "
                "China'"
            ),
            "url": (
                "https://faculty.wharton.upenn.edu/wp-content/uploads/2018/08/"
                "size_value_china_appendix_2_rev.pdf"
            ),
        },
        {
            "source": "PBOC official benchmark RMB deposit rate historical table",
            "url": PBOC_HISTORY_INDEX_URL,
        },
    ],
    "findings": {
        "risk_free_instrument": {
            "result": "CONFIRMED",
            "evidence": (
                "LSY: 'The market factor, MKT, is the return on the "
                "value-weighted portfolio of our universe, the top 70% of "
                "stocks, in excess of the one-year deposit interest rate'; "
                "data section: 'Our series for the riskfree rate, the "
                "one-year deposit rate, is obtained from ... CSMAR ... on "
                "WRDS.'"
            ),
        },
        "pboc_rate_history": {
            "result": "CONFIRMED",
            "evidence": (
                "PBOC's own historical table records 38 effective-date "
                "changes from 1990-04-15 through 2015-10-24; the final "
                "change set the benchmark one-year deposit rate to 1.50%."
            ),
        },
        "announcement_vs_effective": {
            "result": "CONFIRMED",
            "evidence": (
                "The 2015-10-24 change was publicly announced on 2015-10-23 "
                "(effective the next day), confirmed by the independent "
                "cross-check below; PBOC's table itself records the "
                "effective date."
            ),
        },
        "pro_rate_vs_compound": {
            "result": "NOT ESTABLISHED",
            "disposition": (
                "RF DAILY TRANSFORMATION CONVENTION = NOT CERTIFIED. Neither "
                "LSY (2019) nor its Online Appendix states whether the annual "
                "quoted rate is converted by simple pro-rating or by "
                "compounding, nor the exact domestic day-count convention. "
                "The frozen default (simple pro-rating, Actual/365) is "
                "retained and flagged; it is not upgraded by this check and "
                "is not presented as a fact about LSY's paper."
            ),
        },
        "ep_formation_cadence": {
            "result": "CONFIRMED",
            "evidence": (
                "LSY: 'the sort at the end of a given month uses the "
                "information in a firm's financial report having the most "
                "recent public release date prior to that month's end,' with "
                "quarterly reports from 2002-01-01 and semiannual reports "
                "before that. This is exactly the per-formation-date "
                "latest_known_value(as_of=F_m) behavior, with no additional "
                "report-period-type filter."
            ),
        },
        "cumulative_ytd_vs_ttm": {
            "result": "NOT ESTABLISHED",
            "disposition": (
                "Open data-semantics limitation. LSY define earnings as the "
                "'most recently reported net profit excluding nonrecurrent "
                "gains/losses' but do not state whether an interim "
                "cumulative-YTD figure is used as-filed or annualized/TTM-"
                "adjusted. No TTM adjustment is invented by this phase."
            ),
        },
    },
}


def _download(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:
        if response.status != 200:
            raise RuntimeError(f"unexpected HTTP status {response.status} for {url}")
        return response.read()


def _find_latest_deposit_rate_page(index_html: str) -> str:
    """Return the absolute URL of the most recent benchmark-deposit table."""
    for match in re.finditer(
        r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', index_html, re.S
    ):
        href = match.group(1)
        text = unescape(re.sub(r"<[^>]+>", "", match.group(2))).strip()
        if DEPOSIT_LINK_MARKER in text:
            if href.startswith("http"):
                return href
            return PBOC_BASE_URL + href
    raise RuntimeError(
        f"could not find a {DEPOSIT_LINK_MARKER!r} link on {PBOC_HISTORY_INDEX_URL}; "
        "the PBOC page structure may have changed -- report this as a named "
        "finding instead of silently adapting."
    )


def _extract_table_rows(html: str) -> list[list[str]]:
    """Return the parsed cell rows of the benchmark-deposit-rate table.

    The chosen table is the one whose rows are predominantly date-keyed
    (``YYYY.MM.DD``) data rows, so this is robust to the surrounding page's
    navigation tables.
    """
    tables = re.findall(r"<table.*?</table>", html, re.S)
    chosen: list[list[str]] | None = None
    for table in tables:
        parsed: list[list[str]] = []
        for row in re.findall(r"<tr.*?</tr>", table, re.S):
            cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.S)
            cells = [
                re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", "", c))).strip()
                for c in cells
            ]
            parsed.append(cells)
        date_rows = [c for c in parsed if c and re.match(r"^\d{4}\.\d{2}\.\d{2}", c[0])]
        if len(date_rows) > len(parsed) * 0.5 and len(date_rows) > 5:
            chosen = parsed
            break
    if chosen is None:
        raise RuntimeError(
            "could not locate the PBOC benchmark-deposit-rate table; the page "
            "structure may have changed -- report this as a named finding "
            "instead of silently adapting."
        )
    return chosen


def _build_observations(raw_rows: list[list[str]]) -> list[dict[str, object]]:
    """Build the one-year ``(announcement_date, effective_date, rate)`` table."""
    observations: list[dict[str, object]] = []
    for cells in raw_rows:
        if not cells or not re.match(r"^\d{4}\.\d{2}\.\d{2}", cells[0]):
            continue
        if len(cells) < 8:
            raise RuntimeError(
                f"unexpected PBOC rate row with {len(cells)} cells: {cells}; "
                "refusing to record a malformed table."
            )
        raw_date = cells[0].rstrip("*")
        effective = datetime.strptime(raw_date, "%Y.%m.%d").date().isoformat()
        try:
            one_year = float(cells[4])
        except ValueError as exc:
            raise RuntimeError(
                f"unexpected non-numeric one-year rate {cells[4]!r} for "
                f"{effective}; refusing to record a malformed table."
            ) from exc
        announcement = CONFIRMED_ANNOUNCEMENT_DATES.get(effective, effective)
        observations.append(
            {
                "announcement_date": announcement,
                "effective_date": effective,
                "rate": one_year,
            }
        )
    if not observations:
        raise RuntimeError("parsed PBOC table yielded no rate observations")
    return observations


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT_DIR,
        help=f"fixture output directory (default: {DEFAULT_OUT_DIR})",
    )
    args = parser.parse_args()

    print(f"GET {PBOC_HISTORY_INDEX_URL}", flush=True)
    index_bytes = _download(PBOC_HISTORY_INDEX_URL)
    index_html = index_bytes.decode("utf-8", errors="replace")
    page_url = _find_latest_deposit_rate_page(index_html)
    print(f"GET {page_url}", flush=True)
    page_bytes = _download(page_url)
    page_html = page_bytes.decode("utf-8", errors="replace")

    raw_rows = _extract_table_rows(page_html)
    observations = _build_observations(raw_rows)

    latest = observations[-1]
    confirmed = CROSS_CHECK["confirms"]
    if (
        latest["effective_date"] != confirmed["effective_date"]
        or latest["rate"] != confirmed["rate"]
        or latest["announcement_date"] != confirmed["announcement_date"]
    ):
        raise RuntimeError(
            "recorded PBOC table's most recent change "
            f"{latest} does not match the independent cross-check "
            f"{confirmed}; refusing to overwrite the fixture."
        )

    args.out.mkdir(parents=True, exist_ok=True)
    body_filename = "pboc_one_year_deposit_rate_history.json"
    body_obj = {
        "_comment": (
            "P5B-1 fixture body. 'raw_table' is the verbatim parsed cell grid "
            "of PBOC's official benchmark-deposit-rate historical table (date, "
            "活期, 三个月, 半年, 一年, 二年, 三年, 五年); 'observations' is "
            "the benchmark one-year series the provider consumes, as "
            "(announcement_date, effective_date, rate-in-annual-percent)."
        ),
        "_provenance": {
            "live_recorded": True,
            "index_url": PBOC_HISTORY_INDEX_URL,
            "table_url": page_url,
            "source": (
                "People's Bank of China (PBOC) official benchmark RMB deposit "
                "rate historical table"
            ),
            "provenance_class": "ECONOMIC-DEFINITION REPLICATION",
            "exact_source_replication": False,
            "announcement_date_convention": (
                "announcement_date equals effective_date except for the "
                "2015-10-24 change, whose announcement date (2015-10-23) was "
                "independently confirmed; the equality fallback is a "
                "documented conservative convention, not a claim about PBOC's "
                "announcement date."
            ),
        },
        "observations": observations,
        "raw_table": raw_rows,
    }
    body_text = json.dumps(body_obj, indent=2, ensure_ascii=False) + "\n"
    (args.out / body_filename).write_text(body_text, encoding="utf-8")
    body_sha = hashlib.sha256(body_text.encode("utf-8")).hexdigest()

    retrieved_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    manifest = {
        "_provenance": {
            "live_recorded": True,
            "recorded_at": date.today().isoformat(),
            "retrieved_at_utc": retrieved_at,
            "source": (
                "People's Bank of China (PBOC) official historical benchmark "
                "RMB deposit rate table"
            ),
            "instrument": (
                "PBOC benchmark one-year RMB deposit interest rate "
                "(人民币一年期存款基准利率)"
            ),
            "units": "percent per annum",
            "provenance_class": "ECONOMIC-DEFINITION REPLICATION",
            "evidence_categories": [
                "LIVE-RECORDED (PBOC primary-source table fetched once)",
                "CONSTRUCTED (one-year (announcement_date, effective_date, rate) rows built from that table)",
                "FIXTURE-REPLAYED (tests never touch the network)",
            ],
            "retrieval_method": (
                "HTTPS GET of PBOC's public historical-data page; no API key "
                "or credential used or stored"
            ),
            "table_url": page_url,
            "index_url": PBOC_HISTORY_INDEX_URL,
            "n_observations": len(observations),
            "date_range": {
                "first_effective": observations[0]["effective_date"],
                "last_effective": observations[-1]["effective_date"],
            },
            "note": (
                "Offline replay source for tests/test_risk_free_china.py; the "
                "PBOC series itself is never fetched during pytest."
            ),
        },
        "bounded_check": _BOUNDED_CHECK,
        "independent_cross_check": CROSS_CHECK,
        "recordings": {
            body_filename: {
                "body_sha256": body_sha,
                "n_observations": len(observations),
                "n_raw_rows": len(raw_rows),
            }
        },
    }
    (args.out / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    print(f"wrote {body_filename} ({len(observations)} observations)")
    print(f"wrote manifest.json to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
