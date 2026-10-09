"""E-1 tests — Dataset v1.3 SEC EDGAR acquisition software.

The suite is offline.  An autouse guard replaces ``urllib.request.urlopen``,
``socket.socket.connect`` and ``socket.create_connection`` with a raiser and
scrubs the eight provider credentials plus ``SEC_CONTACT`` from the process
environment, so no test can reach the network or read a real contact.  Every
fetcher is constructed with an explicit synthetic contact and an injected
fake transport.

Covers plan §4 (Fetcher row): rate cap; retry policy (429 / 5xx / timeout
only); request cap; resume by SHA-256; contact required, else 0 requests; the
contact absent from all output.  Also covers the §3 candidate computation and
raw-store layout, and both E-1L plans (§6) with the §7 stops.
"""

from __future__ import annotations

import json
import socket
import sys
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from smart_beta.dataset_v1 import edgar_fetch as E

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "edgar" / "fetch"
MUTATIONS = FIXTURES / "mutations.json"

SYNTHETIC_CONTACT = "synthetic-contact@example.invalid"


@pytest.fixture(autouse=True)
def _offline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Forbid network egress and scrub every credential plus the contact."""

    def _refuse(*args: object, **kwargs: object) -> object:
        raise AssertionError("network access is forbidden in E-1 tests")

    monkeypatch.setattr(urllib.request, "urlopen", _refuse)
    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(socket, "create_connection", _refuse)
    for name in (*E.CREDENTIAL_VARIABLES, "SEC_CONTACT"):
        monkeypatch.delenv(name, raising=False)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _scripted(script: dict):
    """A fake transport that pops a scripted response per URL and records calls."""
    calls: list[tuple[str, dict]] = []

    def transport(url: str, headers: dict) -> E.TransportResponse:
        calls.append((url, dict(headers)))
        entry = script[url]
        if isinstance(entry, list):
            entry = entry.pop(0)
        if callable(entry):
            return entry(url)
        return entry

    return transport, calls


def _clock_pair():
    now = [0.0]
    sleeps: list[float] = []

    def clock() -> float:
        return now[0]

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        now[0] += seconds

    return clock, sleep, sleeps


def _fixture_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _submissions_payload() -> dict:
    return json.loads(_fixture_text("submissions/CIK0000320193.json"))


def _fetcher(transport, calls, **kwargs) -> E.SecEdgarFetcher:
    kwargs.setdefault("contact", SYNTHETIC_CONTACT)
    kwargs.setdefault("sleep", lambda seconds: None)
    kwargs.setdefault("now", lambda: datetime(2026, 1, 1, tzinfo=timezone.utc))
    return E.SecEdgarFetcher(transport=transport, **kwargs)


# ---------------------------------------------------------------------------
# Contact boundary (plan §2, §4; mutations E1-M1, E1-M4)
# ---------------------------------------------------------------------------


def test_contact_required_before_any_request() -> None:
    transport, calls = _scripted({})
    with pytest.raises(E.ContactMissing):
        E.SecEdgarFetcher(transport=transport)
    assert calls == []


def test_contact_absent_from_every_written_byte(tmp_path: Path) -> None:
    store = E.RawStore(tmp_path / "staging")
    url = E.COMPANY_TICKERS_URL
    transport, calls = _scripted({url: E.TransportResponse(200, b'{"ok":true}')})
    fetcher = _fetcher(transport, calls, store=store)
    outcome = fetcher.fetch(url, relpath="company_tickers.json")
    assert outcome.outcome == "FETCHED"
    fetcher.write_log()

    # The synthetic contact really was supplied (it rode in the User-Agent),
    # yet it never appears in a written byte.
    assert SYNTHETIC_CONTACT in calls[0][1]["User-Agent"]
    assert SYNTHETIC_CONTACT not in fetcher.log_jsonl()
    assert SYNTHETIC_CONTACT not in store.read("company_tickers.json").decode()
    assert SYNTHETIC_CONTACT not in store.read("fetch_log.jsonl").decode()
    E.assert_no_contact(store.root, SYNTHETIC_CONTACT)


def test_assert_no_contact_fails_closed(tmp_path: Path) -> None:
    (tmp_path / "clean.txt").write_text("hello", encoding="utf-8")
    E.assert_no_contact(tmp_path, SYNTHETIC_CONTACT)
    (tmp_path / "leak.txt").write_text(f"x {SYNTHETIC_CONTACT} y", encoding="utf-8")
    with pytest.raises(E.ContactLeakError):
        E.assert_no_contact(tmp_path, SYNTHETIC_CONTACT)


# ---------------------------------------------------------------------------
# Retriever policy (plan §4; mutations E1-M2, E1-M3)
# ---------------------------------------------------------------------------


def test_fetch_writes_byte_exact_and_records(tmp_path: Path) -> None:
    store = E.RawStore(tmp_path / "staging")
    url = E.COMPANY_TICKERS_URL
    body = b'{"0": {"cik_str": 1}}'
    transport, calls = _scripted({url: E.TransportResponse(200, body)})
    fetcher = _fetcher(transport, calls, store=store)
    outcome = fetcher.fetch(url, relpath="company_tickers.json")

    assert outcome.outcome == "FETCHED"
    assert outcome.sha256 == E.sha256_bytes(body)
    assert store.read("company_tickers.json") == body
    assert fetcher.requests == 1
    records = fetcher.log
    assert len(records) == 1
    assert records[0].http_status == 200
    assert records[0].sha256 == E.sha256_bytes(body)
    assert records[0].bytes == len(body)
    assert records[0].outcome == "OK"
    assert records[0].retrieval_utc == "2026-01-01T00:00:00+00:00"


def test_rate_cap_enforced() -> None:
    urls = [E.COMPANY_TICKERS_URL, E.CIK_LOOKUP_URL]
    script = {url: E.TransportResponse(200, b"{}") for url in urls}
    transport, calls = _scripted(script)
    clock, sleep, sleeps = _clock_pair()
    fetcher = E.SecEdgarFetcher(
        transport=transport,
        contact=SYNTHETIC_CONTACT,
        clock=clock,
        sleep=sleep,
        max_rate=8.0,
    )
    fetcher.fetch(urls[0])
    fetcher.fetch(urls[1])
    assert len(calls) == 2
    assert sleeps == [pytest.approx(0.125)]


def test_retry_policy_is_narrow() -> None:
    url = E.COMPANY_TICKERS_URL
    sleeps: list[float] = []
    tick = [0.0]

    def clock() -> float:
        tick[0] += 1.0
        return tick[0]

    def make(entry, **kwargs):
        transport, calls = _scripted({url: entry})
        fetcher = E.SecEdgarFetcher(
            transport=transport,
            contact=SYNTHETIC_CONTACT,
            clock=clock,
            sleep=lambda seconds: sleeps.append(seconds),
            **kwargs,
        )
        return fetcher, calls

    # 404 and 403 are never retried.
    for status in (403, 404):
        fetcher, calls = make(E.TransportResponse(status))
        assert fetcher.fetch(url).outcome == "UNRETRIEVABLE"
        assert fetcher.requests == 1 and len(calls) == 1
        assert sleeps == []

    # 429 is retried (twice, then succeeds).
    fetcher, calls = make(
        [E.TransportResponse(429), E.TransportResponse(429), E.TransportResponse(200, b"ok")]
    )
    assert fetcher.fetch(url).outcome == "FETCHED"
    assert fetcher.requests == 3
    assert sleeps == [2.0, 8.0]

    # 5xx is retried once.
    sleeps.clear()
    fetcher, _ = make([E.TransportResponse(503), E.TransportResponse(200, b"ok")])
    assert fetcher.fetch(url).outcome == "FETCHED"
    assert fetcher.requests == 2
    assert sleeps == [2.0]

    # A timeout is retried, then succeeds.
    sleeps.clear()

    def _timeout(_url: str) -> E.TransportResponse:
        raise TimeoutError()

    fetcher, _ = make([_timeout, E.TransportResponse(200, b"ok")])
    assert fetcher.fetch(url).outcome == "FETCHED"
    assert fetcher.requests == 2
    assert sleeps == [2.0]

    # A generic transport error is not retried.
    sleeps.clear()

    def _oserror(_url: str) -> E.TransportResponse:
        raise OSError("connection reset")

    fetcher, _ = make(_oserror)
    assert fetcher.fetch(url).outcome == "UNRETRIEVABLE"
    assert fetcher.requests == 1
    assert sleeps == []

    # Retries are exhausted after two backoffs.
    sleeps.clear()
    fetcher, _ = make(
        [E.TransportResponse(500), E.TransportResponse(500), E.TransportResponse(500)]
    )
    assert fetcher.fetch(url).outcome == "UNRETRIEVABLE"
    assert fetcher.requests == 3
    assert sleeps == [2.0, 8.0]


def test_request_cap_is_never_exceeded() -> None:
    urls = [E.COMPANY_TICKERS_URL, E.CIK_LOOKUP_URL, E.submissions_url(320193)]
    transport, calls = _scripted({url: E.TransportResponse(200, b"x") for url in urls})
    fetcher = _fetcher(transport, calls, max_requests=2)
    outcomes = [fetcher.fetch(url) for url in urls]
    assert [outcome.outcome for outcome in outcomes] == [
        "FETCHED",
        "FETCHED",
        "REQUEST_CAP_REACHED",
    ]
    assert len(calls) == 2
    assert fetcher.requests == 2


def test_resume_by_sha256(tmp_path: Path) -> None:
    store = E.RawStore(tmp_path / "staging")
    body = b"abc"
    digest = store.write("company_tickers.json", body)
    url = E.COMPANY_TICKERS_URL

    transport, calls = _scripted({url: E.TransportResponse(200, b"different")})
    fetcher = _fetcher(
        transport,
        calls,
        store=store,
        resume_index={url: digest},
    )
    outcome = fetcher.fetch(url, relpath="company_tickers.json")
    assert outcome.outcome == "CACHED"
    assert outcome.sha256 == digest
    assert fetcher.requests == 0 and calls == []

    # A divergent index entry refetches rather than trusting the stale bytes.
    transport2, calls2 = _scripted({url: E.TransportResponse(200, b"different")})
    fetcher2 = _fetcher(
        transport2,
        calls2,
        store=store,
        resume_index={url: "0" * 64},
    )
    outcome2 = fetcher2.fetch(
        url, relpath="company_tickers.json", expected_sha256="0" * 64
    )
    assert outcome2.outcome == "FETCHED"
    assert fetcher2.requests == 1
    assert store.read("company_tickers.json") == b"different"


def test_non_sec_host_refused() -> None:
    transport, calls = _scripted({})
    fetcher = _fetcher(transport, calls)
    for url in ("http://example.com/x", "http://www.sec.gov/x", "ftp://www.sec.gov/x"):
        with pytest.raises(E.UrlRefused):
            fetcher.fetch(url)
    assert calls == []


def test_fetch_many_deduplicates_and_resumes(tmp_path: Path) -> None:
    store = E.RawStore(tmp_path / "staging")
    url = E.COMPANY_TICKERS_URL
    requests = [
        E.FetchRequest(url, "company_tickers.json", "reference"),
        E.FetchRequest(url, "company_tickers.json", "reference"),
    ]
    transport, calls = _scripted({url: E.TransportResponse(200, b"body")})
    fetcher = _fetcher(transport, calls, store=store)
    outcomes = fetcher.fetch_many(requests)
    assert len(outcomes) == 1
    assert len(calls) == 1


# ---------------------------------------------------------------------------
# Raw store / manifest (plan §3)
# ---------------------------------------------------------------------------


def test_raw_store_manifest_is_deterministic(tmp_path: Path) -> None:
    root = tmp_path / "raw"
    acq_ids: list[str] = []
    for run in range(2):
        store = E.RawStore(tmp_path / f"stage{run}")
        store.write("company_tickers.json", (FIXTURES / "company_tickers.json").read_bytes())
        store.write("cik-lookup-data.txt", (FIXTURES / "cik-lookup-data.txt").read_bytes())
        store.write(
            "submissions/CIK0000320193.json",
            (FIXTURES / "submissions" / "CIK0000320193.json").read_bytes(),
        )
        if run == 0:
            acq_id, final = store.finalize(dest_root=root)
        else:
            acq_id, final = store.finalize()
        acq_ids.append(acq_id)
        manifest = store.verify_manifest()
        assert manifest["acq_id"] == acq_id

    assert acq_ids[0] == acq_ids[1]
    assert (root / f"edgar-{acq_ids[0]}").is_dir()
    manifest = json.loads((root / f"edgar-{acq_ids[0]}" / "manifest.json").read_text())
    assert manifest["acq_id"] == E.compute_acq_id(
        {entry["path"]: entry["sha256"] for entry in manifest["files"]}
    )


# ---------------------------------------------------------------------------
# Candidate-CIK computation (plan §3)
# ---------------------------------------------------------------------------


def test_parse_reference_files() -> None:
    company = E.parse_company_tickers(_fixture_text("company_tickers.json"))
    assert company["AAPL"] == (320193,)
    assert company["MSFT"] == (789019,)
    lookup = E.parse_cik_lookup(_fixture_text("cik-lookup-data.txt"))
    assert lookup["apple"] == (320193,)
    assert lookup["ex why zee"] == (1234567,)


def test_candidate_ciks_ticker_and_name_routes() -> None:
    company = E.parse_company_tickers(_fixture_text("company_tickers.json"))
    lookup = E.parse_cik_lookup(_fixture_text("cik-lookup-data.txt"))
    keys = [
        {"security_key": "K-AAPL", "ticker": "AAPL", "issuer_names": []},
        {"security_key": "K-EXW", "ticker": "EXWQ", "issuer_names": ["Ex Why Zee Corp"]},
        {"security_key": "K-NONE", "ticker": "NOPE", "issuer_names": []},
    ]
    result = E.compute_candidate_ciks(keys, company_tickers=company, cik_lookup=lookup)
    assert result["K-AAPL"] == (320193,)
    assert result["K-EXW"] == (1234567,)
    assert result["K-NONE"] == ()


def test_candidate_name_route_is_capped() -> None:
    lookup = {"acme": (1, 2, 3, 4, 5, 6, 7)}
    keys = [{"security_key": "K", "ticker": None, "issuer_names": ["Acme"]}]
    result = E.compute_candidate_ciks(
        keys, company_tickers={}, cik_lookup=lookup, max_name_candidates=5
    )
    assert result["K"] == (1, 2, 3, 4, 5)


def test_development_top_1000_keys_joins_membership_and_identity() -> None:
    membership = [
        {"security_key": "K1", "formation_date": "2016-01-04", "top_1000": True},
        {"security_key": "K2", "formation_date": "2016-01-04", "top_1000": False},
        {"security_key": "K3", "formation_date": "2023-07-03", "top_1000": True},
        {"security_key": "K4", "formation_date": "2014-12-31", "top_1000": True},
    ]
    d4 = [
        {"security_key": "K1", "ticker": "AAA"},
        {"security_key": "K2", "ticker": "BBB"},
        {"security_key": "K3", "ticker": "CCC"},
        {"security_key": "K4", "ticker": "DDD"},
        {"security_key": "K5", "ticker": "EEE"},
    ]
    keys = E.development_top_1000_keys(membership, d4)
    assert set(keys) == {"K1"}
    assert keys["K1"].ticker == "AAA"


def test_build_l1_plan_stops_over_budget() -> None:
    with pytest.raises(E.RequestBudgetExceeded):
        E.build_l1_plan([320193], max_requests=1)


def test_former_name_followups_adds_lookup_hits() -> None:
    lookup = E.parse_cik_lookup(_fixture_text("cik-lookup-data.txt"))
    result = E.former_name_followups(
        {"K": (320193,)},
        submissions_former_names={320193: ["NeXT Software Inc."]},
        cik_lookup=lookup,
    )
    assert result["K"] == (320193, 888888)


# ---------------------------------------------------------------------------
# E-1L plans (plan §6) and stops (plan §7)
# ---------------------------------------------------------------------------


def test_iter_filings_handles_recent_and_paginated() -> None:
    recent = E.iter_filings(_submissions_payload())
    assert len(recent) == 3
    paginated = E.iter_filings(
        json.loads(
            (
                FIXTURES
                / "submissions"
                / "files"
                / "CIK0000320193-submissions-001.json"
            ).read_text()
        )
    )
    assert len(paginated) == 1
    assert paginated[0]["form"] == "10-K"


def test_build_l1_plan_reference_and_submissions() -> None:
    plan = E.build_l1_plan(
        [320193], paginated_files={320193: ("CIK0000320193-submissions-001.json",)}
    )
    assert [request.relpath for request in plan] == [
        "company_tickers.json",
        "cik-lookup-data.txt",
        "submissions/CIK0000320193.json",
        "submissions/files/CIK0000320193-submissions-001.json",
    ]
    assert [request.kind for request in plan] == [
        "reference",
        "reference",
        "submission",
        "submission_file",
    ]
    assert [request.url for request in plan][:2] == [
        E.COMPANY_TICKERS_URL,
        E.CIK_LOOKUP_URL,
    ]


def test_paginated_requests_from_submissions() -> None:
    names = E.paginated_requests_from_submissions({320193: _submissions_payload()})
    assert names == {320193: ("CIK0000320193-submissions-001.json",)}


def test_build_l2_plan_selects_periodic_filings() -> None:
    sample = [E.SampleKey("K-AAPL", 320193, date(2023, 1, 1), date(2023, 12, 31))]
    plan = E.build_l2_plan(sample, submissions={320193: _submissions_payload()})
    assert len(plan) == 4
    kinds = [request.kind for request in plan]
    assert kinds.count("filing_index") == 2
    assert kinds.count("primary_doc") == 2
    urls = [request.url for request in plan]
    assert any("000032019323000064" in url for url in urls)
    assert any("000032019323000010" in url for url in urls)
    # The 8-K is not a periodic filing and never enters the L2 plan.
    assert not any("000032019323000077" in url for url in urls)


def test_l2_plan_interval_filter_excludes_outside_filings() -> None:
    sample = [E.SampleKey("K-AAPL", 320193, date(2023, 3, 1), date(2023, 12, 31))]
    plan = E.build_l2_plan(sample, submissions={320193: _submissions_payload()})
    # Only the 2023-05-05 10-Q is inside the interval.
    assert len(plan) == 2


def test_l2_plan_stops_over_budget() -> None:
    sample = [E.SampleKey("K-AAPL", 320193, date(2023, 1, 1), date(2023, 12, 31))]
    with pytest.raises(E.RequestBudgetExceeded):
        E.build_l2_plan(
            sample, submissions={320193: _submissions_payload()}, max_requests=1
        )


def test_response_format_stop_on_missing_acceptance() -> None:
    payload = _submissions_payload()
    E.validate_submission_payload(payload)
    broken = json.loads(json.dumps(payload))
    del broken["filings"]["recent"]["acceptanceDateTime"]
    with pytest.raises(E.ResponseFormatError):
        E.validate_submission_payload(broken)


def test_check_request_budget_counts_without_truncating() -> None:
    plan = E.build_l1_plan([320193])
    assert E.check_request_budget(plan, max_requests=len(plan)) == len(plan)
    with pytest.raises(E.RequestBudgetExceeded):
        E.check_request_budget(plan, max_requests=len(plan) - 1)


def test_retry_status_helper() -> None:
    assert E.is_retry_status(429)
    assert E.is_retry_status(500)
    assert E.is_retry_status(503)
    assert E.is_retry_status(599)
    for status in (None, 200, 301, 400, 403, 404, 422):
        assert not E.is_retry_status(status)


def test_url_builders() -> None:
    assert (
        E.submissions_url(320193)
        == "https://data.sec.gov/submissions/CIK0000320193.json"
    )
    assert (
        E.filing_index_url(320193, "0000320193-23-000064")
        == "https://www.sec.gov/Archives/edgar/data/320193/000032019323000064/index.json"
    )
    assert (
        E.primary_document_url(320193, "0000320193-23-000064", "aapl.htm")
        == "https://www.sec.gov/Archives/edgar/data/320193/000032019323000064/aapl.htm"
    )


# ---------------------------------------------------------------------------
# L3 index-header plan (erratum V13-E2 §5) and the strict header parser
# ---------------------------------------------------------------------------


def _l3_payload() -> dict:
    return json.loads(
        (
            FIXTURES / "l3" / "submissions" / "CIK0001234567.json"
        ).read_text(encoding="utf-8")
    )


def test_build_l3_plan_selects_event_typed_filings() -> None:
    scope = {1234567: (date(2015, 1, 2), date(2023, 6, 30))}
    plan = E.build_l3_plan(
        scope, submissions={1234567: _l3_payload()}, max_requests=100
    )
    # The 8-K without 2.02, the two /A amendments, the pre-2015 8-K, the
    # out-of-interval 10-Q and the DEF 14A are all excluded.  Every one of
    # the six event-typed forms yields exactly one header request.
    assert [request.accession for request in plan] == [
        "0001234567-16-000001",  # original 8-K with Item 2.02
        "0001234567-16-000004",  # 10-Q
        "0001234567-17-000001",  # 10-K
        "0001234567-18-000001",  # 6-K
        "0001234567-19-000001",  # 20-F
        "0001234567-20-000001",  # 40-F
    ]
    assert all(request.kind == "header" for request in plan)
    assert all(request.cik == 1234567 for request in plan)


def test_build_l3_plan_relpath_and_url_format() -> None:
    scope = {1234567: (date(2015, 1, 2), date(2023, 6, 30))}
    plan = E.build_l3_plan(
        scope, submissions={1234567: _l3_payload()}, max_requests=100
    )
    first = plan[0]
    assert first.relpath == (
        "headers/0001234567/0001234567-16-000001-index-headers.html"
    )
    assert first.url == (
        "https://www.sec.gov/Archives/edgar/data/1234567/000123456716000001/"
        "0001234567-16-000001-index-headers.html"
    )
    # The relpath helper is the frozen E-3-R1 interface (addendum to E-1-R1).
    assert E.headers_relpath(320193, "0000320193-23-000064") == (
        "headers/0000320193/0000320193-23-000064-index-headers.html"
    )
    assert E.filing_headers_url(320193, "0000320193-23-000064") == (
        "https://www.sec.gov/Archives/edgar/data/320193/000032019323000064/"
        "0000320193-23-000064-index-headers.html"
    )


def test_build_l3_plan_respects_floor_and_interval() -> None:
    payload = _l3_payload()

    def accessions(plan):
        return [request.accession for request in plan]

    # The 2014-12-01 8-K is below the 2015-01-02 floor and the 2024-01-01
    # 10-Q is after the interval end.
    full = E.build_l3_plan(
        {1234567: (date(2015, 1, 2), date(2023, 6, 30))},
        submissions={1234567: payload},
        max_requests=100,
    )
    assert "0001234567-14-000001" not in accessions(full)
    assert "0001234567-24-000001" not in accessions(full)
    assert len(full) == 6

    # A narrow interval keeps only the 2016 event filings.
    narrow = E.build_l3_plan(
        {1234567: (date(2016, 1, 1), date(2016, 12, 31))},
        submissions={1234567: payload},
        max_requests=100,
    )
    assert accessions(narrow) == [
        "0001234567-16-000001",
        "0001234567-16-000004",
    ]


def test_build_l3_plan_accepts_sample_keys() -> None:
    sample = [
        E.SampleKey("K-L3", 1234567, date(2015, 1, 2), date(2023, 6, 30))
    ]
    plan = E.build_l3_plan(
        sample, submissions={1234567: _l3_payload()}, max_requests=100
    )
    assert len(plan) == 6


def test_build_l3_plan_stops_over_budget() -> None:
    with pytest.raises(E.RequestBudgetExceeded):
        E.build_l3_plan(
            {1234567: (date(2015, 1, 2), date(2023, 6, 30))},
            submissions={1234567: _l3_payload()},
            max_requests=1,
        )


def test_parse_acceptance_header_reads_et_wall_clock() -> None:
    text = (
        FIXTURES / "headers" / "0001234567-16-000001-index-headers.html"
    ).read_text(encoding="utf-8")
    parsed = E.parse_acceptance_header(text)
    assert parsed == datetime(2016, 3, 15, 20, 30, 0, tzinfo=E.EASTERN)
    assert parsed.tzinfo == E.EASTERN
    assert parsed.utcoffset() is not None
    assert parsed.utcoffset().total_seconds() == -4 * 3600
    # A bytes body parses identically, and the alias matches the canonical name.
    assert E.parse_acceptance_datetime_header(text.encode("utf-8")) == parsed


def test_parse_acceptance_header_is_strict() -> None:
    missing = (
        FIXTURES / "headers" / "0001234567-16-000002-index-headers.html"
    ).read_text(encoding="utf-8")
    assert E.parse_acceptance_header(missing) is None
    bad_values = [
        "",
        "no tag at all",
        "<ACCEPTANCE-DATETIME>2023050516302",       # 13 digits
        "<ACCEPTANCE-DATETIME>202305051630281",      # 15 digits
        "<ACCEPTANCE-DATETIME>2023-05-05T16:30:28",  # hyphenated
        "<ACCEPTANCE-DATETIME>20231305163028",       # month 13
        "<ACCEPTANCE-DATETIME>2023050516302x",       # trailing non-digit
        "<ACCEPTANCE-DATETIME>ab",                   # non-numeric
    ]
    for value in bad_values:
        assert E.parse_acceptance_header(value) is None, value
    assert E.parse_acceptance_header(None) is None  # type: ignore[arg-type]


def test_iter_filings_handles_top_level_parallel_arrays() -> None:
    # The real SEC paginated ``files/*.json`` shape: parallel arrays at the
    # top level, no wrapping ``filings`` object (V13-E2 addendum).
    body = {
        "accessionNumber": ["A-1", "A-2"],
        "form": ["10-K", "8-K"],
        "filingDate": ["2016-10-26", "2016-11-01"],
    }
    rows = E.iter_filings(body)
    assert [row["accessionNumber"] for row in rows] == ["A-1", "A-2"]
    assert rows[0]["form"] == "10-K"


def test_validate_submission_payload_requires_real_name_key() -> None:
    payload = _submissions_payload()
    assert "name" in payload and "entityName" not in payload
    E.validate_submission_payload(payload)
    broken = json.loads(json.dumps(payload))
    del broken["name"]
    with pytest.raises(E.ResponseFormatError):
        E.validate_submission_payload(broken)


# ---------------------------------------------------------------------------
# Mutation contract
# ---------------------------------------------------------------------------


def test_mutation_fixture_contract() -> None:
    mutations = json.loads(MUTATIONS.read_text(encoding="utf-8"))
    assert [mutation["id"] for mutation in mutations] == [
        "CONTROL",
        "E1-M1",
        "E1-M2",
        "E1-M3",
        "E1-M4",
        "E1-M5",
    ]
    source = (REPO_ROOT / "smart_beta" / "dataset_v1" / "edgar_fetch.py").read_text(
        encoding="utf-8"
    )
    this_module = sys.modules[__name__]
    for mutation in mutations:
        assert mutation["file"] == "smart_beta/dataset_v1/edgar_fetch.py"
        assert mutation["description"]
        module_name, _, test_name = mutation["test"].partition("::")
        assert module_name == "tests/test_edgar_fetch.py"
        assert hasattr(this_module, test_name), mutation["id"]
        if mutation["id"] == "CONTROL":
            assert mutation["edits"] == []
            continue
        assert mutation["edits"], mutation["id"]
        for edit in mutation["edits"]:
            assert edit["old"] and edit["new"] is not None
            assert source.count(edit["old"]) == 1, (mutation["id"], edit["old"])
            assert edit["old"] != edit["new"], mutation["id"]
