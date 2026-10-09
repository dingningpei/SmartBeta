"""E-3 tests — Dataset v1.3 SEC EDGAR event normalization, timing and firewall.

The suite is offline.  An autouse guard replaces ``urllib.request.urlopen``,
``socket.socket.connect`` and ``socket.create_connection`` with a raiser and
scrubs the eight provider credentials plus ``SEC_CONTACT`` from the process
environment, so no test can reach the network.  Every event is built from
synthetic fixtures only; no test opens ``data_store``.

Covers plan §4 (Timing, Event types, Firewall rows), the V13-E2 erratum
(header time source, ``acceptance_json_raw`` / ``json_discrepancy``, the
``EVENT_TIME_UNAVAILABLE`` path), the V13-E4 §2 erratum (a missing
``header_times`` mapping fails closed; the JSON-time path is removed) and
every plan §5 / erratum mutation with the ``E3`` prefix.
"""

from __future__ import annotations

import json
import socket
import sys
import urllib.request
from datetime import date, datetime, time, timezone
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq
import pytest

from smart_beta.dataset_v1 import edgar_events as E
from smart_beta.dataset_v1 import edgar_fetch as F
from smart_beta.vendors.tiingo.calendar_source import build_nyse_calendar

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "edgar" / "events"
MUTATIONS = FIXTURES / "mutations.json"

#: The real SEC ``filings.recent`` field list (V13-E2 Addendum).
FILING_FIELDS = (
    "accessionNumber",
    "act",
    "core_type",
    "fileNumber",
    "filingDate",
    "filmNumber",
    "form",
    "isInlineXBRL",
    "isXBRL",
    "isXBRLNumeric",
    "items",
    "primaryDocDescription",
    "primaryDocument",
    "reportDate",
    "size",
    "acceptanceDateTime",
)

#: Sentinel: build the header mapping from each row's JSON ET wall clock.
_AUTO_HEADERS = object()


@pytest.fixture(autouse=True)
def _offline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Forbid network egress and scrub every credential plus the contact."""

    def _refuse(*args: object, **kwargs: object) -> object:
        raise AssertionError("network access is forbidden in E-3 tests")

    monkeypatch.setattr(urllib.request, "urlopen", _refuse)
    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(socket, "create_connection", _refuse)
    for name in (*F.CREDENTIAL_VARIABLES, "SEC_CONTACT"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(scope="module")
def calendar():
    """The trusted 2015-2027 session calendar, built once."""
    return build_nyse_calendar("2015-01-01", "2027-12-31")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _filing(**overrides: object) -> dict:
    row = {
        "accessionNumber": "0000000001-22-000001",
        "act": "34",
        "core_type": "8-K",
        "fileNumber": "001-00001",
        "filingDate": "2022-03-15",
        "filmNumber": "22700001",
        "form": "8-K",
        "isInlineXBRL": 0,
        "isXBRL": 0,
        "isXBRLNumeric": 0,
        "items": "2.02",
        "primaryDocDescription": "8-K",
        "primaryDocument": "doc.htm",
        "reportDate": "2022-03-15",
        "size": 12345,
        "acceptanceDateTime": "2022-03-15T14:00:00.000Z",
    }
    row.update(overrides)
    return row


def _payload(rows, cik: int = 1, entity_name: str = "Synthetic Corp.") -> dict:
    recent = {field: [row.get(field) for row in rows] for field in FILING_FIELDS}
    return {
        "cik": str(cik),
        "entityType": "operating",
        "sic": "7372",
        "sicDescription": "Services-Prepackaged Software",
        "name": entity_name,
        "tickers": ["SYN"],
        "exchanges": ["Nasdaq"],
        "formerNames": [],
        "filings": {"recent": recent, "files": []},
    }


def _header_clock(value: str) -> str:
    """The ET wall-clock 14-digit field equivalent to a JSON value."""
    return E.to_eastern(E.parse_acceptance_datetime(value)).strftime("%Y%m%d%H%M%S")


def _default_headers(rows) -> dict:
    """A per-accession header mapping consistent with each row's JSON value."""
    headers: dict[str, str] = {}
    for row in rows:
        value = row.get("acceptanceDateTime")
        if value is None:
            continue
        headers[str(row.get("accessionNumber") or "")] = _header_clock(value)
    return headers


def _normalize(rows, calendar, *, cik: int = 1, key: str = "K1", header_times=_AUTO_HEADERS, **kwargs):
    payload = _payload(rows, cik=cik)
    if header_times is _AUTO_HEADERS:
        header_times = _default_headers(rows)
    return E.normalize_events(
        [E.KeyCik(key, cik)],
        {cik: payload},
        calendar=calendar,
        header_times=header_times,
        validate=False,
        **kwargs,
    )


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _types_for(rows, calendar, accession: str) -> list[str]:
    return [
        event.event_type
        for event in _normalize(rows, calendar)
        if event.accession == accession
    ]


# ---------------------------------------------------------------------------
# Timing (plan §4; mutations E3-M1, E3-M2, E3-M7)
# ---------------------------------------------------------------------------


def test_k_session_boundary_1600(calendar) -> None:
    """15:59:59 ET stays; 16:00:00 ET moves to the next session."""
    before = _filing(
        accessionNumber="A-BEFORE",
        acceptanceDateTime="2022-03-15T19:59:59.000Z",  # 15:59:59 EDT
    )
    after = _filing(
        accessionNumber="A-AFTER",
        acceptanceDateTime="2022-03-15T20:00:00.000Z",  # 16:00:00 EDT
    )
    events = {event.accession: event for event in _normalize([before, after], calendar)}
    assert events["A-BEFORE"].k_session == date(2022, 3, 15)
    assert events["A-AFTER"].k_session == date(2022, 3, 16)
    assert events["A-BEFORE"].acceptance_et.time() == time(15, 59, 59)
    assert events["A-AFTER"].acceptance_et.time() == time(16, 0, 0)


def test_filing_date_is_never_the_knowledge_time(calendar) -> None:
    """K(e) comes from the index header, never from filingDate."""
    row = _filing(
        filingDate="2022-03-15",
        acceptanceDateTime="2022-03-15T20:30:00.000Z",  # 16:30 EDT, after close
    )
    (event,) = _normalize([row], calendar)
    assert event.k_session == date(2022, 3, 16)
    assert event.acceptance_et.hour == 16 and event.acceptance_et.minute == 30


def test_dst_transition_changes_the_wall_clock_hour(calendar) -> None:
    """The same UTC hour is before the close in EST and after it in EDT."""
    winter = _filing(
        accessionNumber="A-WINTER",
        filingDate="2022-03-11",
        reportDate="2022-03-11",
        acceptanceDateTime="2022-03-11T21:00:00.000Z",  # 16:00 EST exactly
    )
    spring = _filing(
        accessionNumber="A-SPRING",
        filingDate="2022-03-14",
        reportDate="2022-03-14",
        acceptanceDateTime="2022-03-14T19:00:00.000Z",  # 15:00 EDT
    )
    events = {
        event.accession: event
        for event in _normalize([winter, spring], calendar)
    }
    assert events["A-WINTER"].acceptance_et.utcoffset().total_seconds() == -5 * 3600
    assert events["A-SPRING"].acceptance_et.utcoffset().total_seconds() == -4 * 3600
    assert events["A-WINTER"].k_session == date(2022, 3, 14)
    assert events["A-SPRING"].k_session == date(2022, 3, 14)


def test_weekend_and_holiday_roll_to_the_next_session(calendar) -> None:
    saturday = _filing(
        accessionNumber="A-SAT",
        filingDate="2022-01-15",
        reportDate="2022-01-15",
        acceptanceDateTime="2022-01-15T15:00:00.000Z",  # Saturday
    )
    holiday = _filing(
        accessionNumber="A-MLK",
        filingDate="2022-01-17",
        reportDate="2022-01-17",
        acceptanceDateTime="2022-01-17T15:00:00.000Z",  # MLK holiday
    )
    events = {
        event.accession: event
        for event in _normalize([saturday, holiday], calendar)
    }
    # Jan 17 2022 is MLK; the next session is Tuesday Jan 18.
    assert events["A-SAT"].k_session == date(2022, 1, 18)
    assert events["A-MLK"].k_session == date(2022, 1, 18)


def test_early_close_session_keeps_the_frozen_1600_boundary(calendar) -> None:
    """An early-close day is a session; the frozen K(e) rule still keys on 16:00."""
    # 2018-11-23 (day after Thanksgiving) is a trading day with a 13:00 close.
    assert calendar.is_trading_day("2018-11-23")
    before = _filing(
        accessionNumber="A-HALF-BEFORE",
        filingDate="2018-11-23",
        reportDate="2018-11-23",
        acceptanceDateTime="2018-11-23T19:00:00.000Z",  # 14:00 EST
    )
    after = _filing(
        accessionNumber="A-HALF-AFTER",
        filingDate="2018-11-23",
        reportDate="2018-11-23",
        acceptanceDateTime="2018-11-23T21:30:00.000Z",  # 16:30 EST
    )
    events = {event.accession: event for event in _normalize([before, after], calendar)}
    assert events["A-HALF-BEFORE"].k_session == date(2018, 11, 23)
    assert events["A-HALF-AFTER"].k_session == date(2018, 11, 26)


def test_boundary_flag_uses_an_injected_early_close(calendar) -> None:
    """B_e is the ±15 min window around the session close, early close included."""
    et = E.to_eastern(E.parse_acceptance_datetime("2018-11-23T18:05:00.000Z"))
    assert et.time() == time(13, 5)
    # The frozen 16:00 close is not near 13:05.
    assert E.boundary_flag(et, calendar) is False
    # An injected early close of 13:00 makes 13:05 a boundary fact.
    early = {date(2018, 11, 23): time(13, 0)}
    assert E.boundary_flag(et, calendar, early) is True
    # A non-session never carries a boundary fact.
    weekend = E.to_eastern(E.parse_acceptance_datetime("2018-11-24T18:05:00.000Z"))
    assert E.boundary_flag(weekend, calendar, early) is False


# ---------------------------------------------------------------------------
# V13-E2 header time source (mutations E3-M6, E3-M7)
# ---------------------------------------------------------------------------


def test_header_is_the_knowledge_source_not_json(calendar) -> None:
    """When a header mapping is supplied, K(e) follows the header, not the JSON."""
    row = _filing(
        accessionNumber="A-HDR",
        acceptanceDateTime="2022-03-15T20:30:00.000Z",  # 16:30 EDT (after close)
    )
    (event,) = _normalize(
        [row], calendar, header_times={"A-HDR": "20220315140000"}
    )  # 14:00 EDT, before close
    assert event.acceptance_et.time() == time(14, 0, 0)
    assert event.k_session == date(2022, 3, 15)
    assert event.acceptance_json_raw == "2022-03-15T20:30:00.000Z"


def test_header_is_read_as_eastern_wall_clock(calendar) -> None:
    """The 14-digit field is an America/New_York wall clock, never UTC."""
    row = _filing(
        accessionNumber="A-AAPL",
        acceptanceDateTime="2022-07-29T23:30:28.000Z",
    )
    (event,) = _normalize(
        [row], calendar, header_times={"A-AAPL": "20220729153028"}
    )
    assert event.acceptance_et == datetime(2022, 7, 29, 15, 30, 28, tzinfo=E.EASTERN)
    assert event.acceptance_utc == datetime(2022, 7, 29, 19, 30, 28, tzinfo=timezone.utc)
    assert event.k_session == date(2022, 7, 29)


def test_missing_header_gives_event_time_unavailable(calendar) -> None:
    """A missing header never falls back to the JSON or filingDate."""
    row = _filing(accessionNumber="A-NOHDR")
    (event,) = _normalize([row], calendar, header_times={})
    assert event.k_session is None
    assert event.acceptance_utc is None and event.acceptance_et is None
    assert event.k_reason == E.EVENT_TIME_UNAVAILABLE
    assert event.json_discrepancy is None
    assert event.acceptance_json_raw == "2022-03-15T14:00:00.000Z"


def test_unparseable_header_gives_event_time_unavailable(calendar) -> None:
    row = _filing(accessionNumber="A-BADHDR")
    (event,) = _normalize(
        [row],
        calendar,
        header_times={"A-BADHDR": "<ACCEPTANCE-DATETIME>2022-03-15T14:00:00"},
    )
    assert event.k_session is None
    assert event.k_reason == E.EVENT_TIME_UNAVAILABLE


def test_index_header_parser_is_strict() -> None:
    assert E.parse_index_header_acceptance(None) is None
    assert E.parse_index_header_acceptance("20220315140000") == "20220315140000"
    assert (
        E.parse_index_header_acceptance("<ACCEPTANCE-DATETIME>20220315140000")
        == "20220315140000"
    )
    assert (
        E.parse_index_header_acceptance("<ACCEPTANCE-DATETIME>202203151400000")
        is None
    )
    assert (
        E.parse_index_header_acceptance("<ACCEPTANCE-DATETIME>2022-03-15T14:00:00")
        is None
    )
    assert E.parse_index_header_acceptance("") is None
    assert E.parse_header_wall_clock("20220230120000") is None  # impossible day


def test_load_raw_header_times_reads_the_l3_relpath() -> None:
    headers = E.load_raw_header_times(FIXTURES, 1)
    assert headers["0000000001-22-000001"] == "20220125160500"
    assert headers["0000000001-22-000002"] == "20220128113000"
    assert E.header_relpath(1, "0000000001-22-000001").as_posix() == (
        "headers/0000000001/0000000001-22-000001-index-headers.html"
    )
    missing = E.load_raw_header_times(FIXTURES, 1, ["0000000001-99-999999"])
    assert missing == {"0000000001-99-999999": None}


def test_json_discrepancy_classes(calendar) -> None:
    """The recorded JSON value is classified against the header time."""
    exact = _filing(
        accessionNumber="A-EXACT",
        acceptanceDateTime="2022-01-25T21:05:00.000Z",
    )
    (event,) = _normalize([exact], calendar, header_times={"A-EXACT": "20220125160500"})
    assert event.json_discrepancy == E.JSON_EXACT_UTC

    other = _filing(
        accessionNumber="A-OTHER",
        acceptanceDateTime="2022-01-25T13:05:00.000Z",
    )
    (event,) = _normalize([other], calendar, header_times={"A-OTHER": "20220125160500"})
    assert event.json_discrepancy == E.JSON_OTHER

    absent = _filing(accessionNumber="A-NOJSON", acceptanceDateTime=None)
    (event,) = _normalize(
        [absent], calendar, header_times={"A-NOJSON": "20220125160500"}
    )
    assert event.acceptance_json_raw is None
    assert event.json_discrepancy is None


def test_json_discrepancy_plus_one_offset(calendar) -> None:
    """A JSON value one ET offset later than the header is PLUS_ONE_OFFSET."""
    row = _filing(
        accessionNumber="A-PLUS",
        filingDate="2022-04-29",
        reportDate="2022-04-29",
        acceptanceDateTime="2022-04-29T23:30:00.000Z",  # header 19:30Z + 4 h
    )
    (event,) = _normalize([row], calendar, header_times={"A-PLUS": "20220429153000"})
    assert event.acceptance_utc == datetime(2022, 4, 29, 19, 30, tzinfo=timezone.utc)
    assert event.json_discrepancy == E.JSON_PLUS_ONE_OFFSET


def test_header_times_is_required(calendar) -> None:
    """A missing header source fails closed; no JSON-time path remains."""
    row = _filing(accessionNumber="A-REQ")
    payload = _payload([row], cik=1)
    with pytest.raises(E.EventInputError):
        E.normalize_key(
            "K1", 1, [row], calendar=calendar, header_times=None
        )
    with pytest.raises(E.EventInputError):
        E.normalize_events(
            [E.KeyCik("K1", 1)],
            {1: payload},
            calendar=calendar,
            header_times=None,
            validate=False,
        )


# ---------------------------------------------------------------------------
# Event types (plan §4; mutations E3-M3, E3-M5)
# ---------------------------------------------------------------------------


def test_item_202_parsed_from_items_string(calendar) -> None:
    cases = {
        "2.02": E.EA_8K202,
        "2.02,9.01": E.EA_8K202,
        "9.01,2.02": E.EA_8K202,
        "9.01": None,
        "2.020": None,
        "": None,
        None: None,
    }
    for items, expected in cases.items():
        row = _filing(accessionNumber=f"A-{items}", items=items)
        events = _normalize([row], calendar)
        if expected is None:
            assert events == [], items
        else:
            assert [event.event_type for event in events] == [expected], items


def test_amendment_never_creates_an_event(calendar) -> None:
    """8-K/A and 10-Q/A never create (or move) an event."""
    row = _filing(
        accessionNumber="A-8KA",
        form="8-K/A",
        items="2.02",
        acceptanceDateTime="2022-03-15T14:00:00.000Z",
    )
    amended = _filing(
        accessionNumber="A-10QA",
        form="10-Q/A",
        items="",
        acceptanceDateTime="2022-03-15T14:00:00.000Z",
    )
    assert _normalize([row, amended], calendar) == []


def test_periodic_fallback_is_typed_separately(calendar) -> None:
    """A 10-Q/10-K with no EA_8K202 in its window is EA_PERIODIC_FALLBACK."""
    periodic = _filing(
        accessionNumber="A-10Q",
        form="10-Q",
        items="",
        filingDate="2022-04-29",
        reportDate="2022-03-31",
        acceptanceDateTime="2022-04-29T19:30:00.000Z",
    )
    (event,) = _normalize([periodic], calendar)
    assert event.event_type == E.EA_PERIODIC_FALLBACK
    assert event.event_type != E.EA_8K202


def test_periodic_fallback_window(calendar) -> None:
    """The fallback window is [report period end, the periodic filing date]."""
    periodic = _filing(
        accessionNumber="A-10Q",
        form="10-Q",
        items="",
        filingDate="2022-01-28",
        reportDate="2021-12-31",
        acceptanceDateTime="2022-01-28T16:30:00.000Z",
    )
    in_window = _filing(
        accessionNumber="A-8K-IN",
        filingDate="2022-01-25",
        reportDate="2022-01-25",
        acceptanceDateTime="2022-01-25T21:05:00.000Z",
    )
    too_early = _filing(
        accessionNumber="A-8K-EARLY",
        filingDate="2021-12-01",
        reportDate="2021-12-01",
        acceptanceDateTime="2021-12-01T21:05:00.000Z",
    )
    too_late = _filing(
        accessionNumber="A-8K-LATE",
        filingDate="2022-02-20",
        reportDate="2022-02-20",
        acceptanceDateTime="2022-02-20T21:05:00.000Z",
    )
    # No 8-K at all: fallback.
    assert _types_for([periodic], calendar, "A-10Q") == [E.EA_PERIODIC_FALLBACK]
    # 8-K inside the window suppresses the fallback (the 8-K is the only event).
    assert _types_for([periodic, in_window], calendar, "A-10Q") == []
    assert _types_for([periodic, in_window], calendar, "A-8K-IN") == [E.EA_8K202]
    # 8-K outside the window does not suppress it.
    assert _types_for([periodic, too_early], calendar, "A-10Q") == [
        E.EA_PERIODIC_FALLBACK
    ]
    assert _types_for([periodic, too_late], calendar, "A-10Q") == [
        E.EA_PERIODIC_FALLBACK
    ]


def test_fpi_typing(calendar) -> None:
    """6-K and 20-F/40-F filers get the separate FPI types."""
    rows = [
        _filing(accessionNumber="F-6K", form="6-K", items=""),
        _filing(accessionNumber="F-20F", form="20-F", items=""),
        _filing(accessionNumber="F-40F", form="40-F", items=""),
    ]
    events = {event.accession: event for event in _normalize(rows, calendar)}
    assert events["F-6K"].event_type == E.FPI_6K
    assert events["F-20F"].event_type == E.FPI_20F
    assert events["F-40F"].event_type == E.FPI_20F


def test_unknown_forms_are_not_events(calendar) -> None:
    rows = [
        _filing(accessionNumber="X-1", form="DEF 14A", items=""),
        _filing(accessionNumber="X-2", form="S-1", items=""),
        _filing(accessionNumber="X-3", form="4", items=""),
    ]
    assert _normalize(rows, calendar) == []


# ---------------------------------------------------------------------------
# Fixtures end to end
# ---------------------------------------------------------------------------


def test_fixtures_use_the_real_sec_schema() -> None:
    for cik in (1, 2):
        payload = _load_fixture(f"submissions/CIK{cik:010d}.json")
        assert "name" in payload and "entityName" not in payload
        assert set(FILING_FIELDS) <= set(payload["filings"]["recent"])


def test_domestic_fixture_end_to_end(calendar) -> None:
    payload = _load_fixture("submissions/CIK0000000001.json")
    headers = E.load_raw_header_times(FIXTURES, 1)
    events = E.normalize_events(
        [E.KeyCik("K-DOM", 1)],
        {1: payload},
        calendar=calendar,
        header_times=headers,
        validate=False,
    )
    by_accession = {event.accession: event for event in events}
    assert set(by_accession) == {
        "0000000001-22-000001",
        "0000000001-22-000003",
    }
    eightk = by_accession["0000000001-22-000001"]
    assert eightk.event_type == E.EA_8K202
    assert eightk.k_session == date(2022, 1, 26)
    assert eightk.json_discrepancy == E.JSON_EXACT_UTC
    fallback = by_accession["0000000001-22-000003"]
    assert fallback.event_type == E.EA_PERIODIC_FALLBACK
    assert fallback.k_session == date(2022, 4, 29)
    assert fallback.report_period == date(2022, 3, 31)
    assert fallback.json_discrepancy == E.JSON_PLUS_ONE_OFFSET


def test_fpi_fixture_end_to_end(calendar) -> None:
    payload = _load_fixture("submissions/CIK0000000002.json")
    headers = E.load_raw_header_times(FIXTURES, 2)
    events = E.normalize_events(
        [E.KeyCik("K-FPI", 2)],
        {2: payload},
        calendar=calendar,
        header_times=headers,
        validate=False,
    )
    types = sorted(event.event_type for event in events)
    assert types == sorted([E.FPI_6K, E.FPI_20F, E.FPI_20F])


def test_raw_layout_reader_follows_pagination(calendar, tmp_path: Path) -> None:
    """The reader flattens the main response and its files/*.json pagination."""
    raw = tmp_path / "raw"
    (raw / "submissions" / "files").mkdir(parents=True)
    (raw / "submissions" / "CIK0000000003.json").write_text(
        json.dumps(
            {
                "cik": "3",
                "name": "Paginated Corp.",
                "filings": {
                    "recent": {field: [] for field in FILING_FIELDS},
                    "files": [
                        {"name": "CIK0000000003-submissions-001.json"}
                    ],
                },
            }
        ),
        encoding="utf-8",
    )
    (raw / "submissions" / "files" / "CIK0000000003-submissions-001.json").write_text(
        json.dumps({"filings": [_filing(accessionNumber="A-PAGE")]}),
        encoding="utf-8",
    )
    rows = E.load_raw_filings(raw, 3)
    assert [row["accessionNumber"] for row in rows] == ["A-PAGE"]
    events = E.normalize_events(
        [E.KeyCik("K-PAGE", 3)],
        {3: rows},
        calendar=calendar,
        header_times={"A-PAGE": "20220315100000"},
        validate=False,
    )
    assert [event.event_type for event in events] == [E.EA_8K202]


# ---------------------------------------------------------------------------
# Firewall (plan §3, §4; mutation E3-M4)
# ---------------------------------------------------------------------------


def _reserve_event(calendar):
    """An acceptance after the close on 2023-06-30 maps to 2023-07-03."""
    row = _filing(
        accessionNumber="A-RESERVE",
        filingDate="2023-06-30",
        reportDate="2023-06-30",
        acceptanceDateTime="2023-06-30T21:00:00.000Z",  # 17:00 EDT
    )
    (event,) = _normalize([row], calendar)
    assert event.k_session == E.RESERVE_START
    return event


def test_reserve_row_never_written_to_development_path(
    calendar, tmp_path: Path
) -> None:
    development = _filing(accessionNumber="A-DEV")
    dev_rows = _normalize([development], calendar)
    reserve = _reserve_event(calendar)
    panel = E.write_event_panel(tmp_path, "synthetic", [*dev_rows, reserve])

    table = pq.read_table(panel.development_path).to_pandas()
    sessions = [value for value in table["k_session"]]
    assert sessions and all(value < E.RESERVE_START for value in sessions)
    assert panel.development_rows == 1
    assert panel.reserve_path is not None and panel.reserve_rows == 1
    reserve_table = pq.read_table(panel.reserve_path).to_pandas()
    assert [value for value in reserve_table["k_session"]] == [E.RESERVE_START]


def test_split_by_firewall_boundary(calendar) -> None:
    reserve = _reserve_event(calendar)
    development = _normalize([_filing(accessionNumber="A-DEV")], calendar)[0]
    dev, res = E.split_by_firewall([reserve, development])
    assert dev == [development]
    assert res == [reserve]


def test_unavailable_time_row_is_not_reserve(calendar) -> None:
    """An EVENT_TIME_UNAVAILABLE row has no session and is not reserve data."""
    (event,) = _normalize(
        [_filing(accessionNumber="A-NOHDR")], calendar, header_times={}
    )
    dev, res = E.split_by_firewall([event])
    assert dev == [event]
    assert res == []


def test_development_loader_reads_unavailable_rows(calendar, tmp_path: Path) -> None:
    """A null (EVENT_TIME_UNAVAILABLE) row is a development row, not a crash."""
    (event,) = _normalize(
        [_filing(accessionNumber="A-NOHDR")], calendar, header_times={}
    )
    E.write_event_panel(tmp_path, "synthetic", [event])
    accessor = E.EventDevelopmentAccessor(tmp_path)
    frame = accessor.read()
    assert len(frame) == 1
    assert pd.isna(frame.loc[0, "k_session"])
    assert frame.loc[0, "k_reason"] == E.EVENT_TIME_UNAVAILABLE


def test_development_loader_fails_closed_on_reserve_row(tmp_path: Path) -> None:
    reserve = _reserve_event(build_nyse_calendar("2015-01-01", "2027-12-31"))
    E.write_events_parquet(tmp_path / "events.parquet", [reserve])
    accessor = E.EventDevelopmentAccessor(tmp_path)
    with pytest.raises(E.EventFirewallError):
        accessor.read()


def test_development_loader_refuses_reserve_range_before_opening(
    calendar, tmp_path: Path
) -> None:
    E.write_event_panel(tmp_path, "synthetic", _normalize([_filing()], calendar))
    accessor = E.EventDevelopmentAccessor(tmp_path)
    with pytest.raises(E.EventFirewallError):
        accessor.read(end=date(2023, 7, 3))
    assert accessor.opened_paths == []


def test_file_open_audit_has_zero_reserve_reads(calendar, tmp_path: Path) -> None:
    E.write_event_panel(tmp_path, "synthetic", _normalize([_filing()], calendar))
    audit: list[Path] = []
    accessor = E.EventDevelopmentAccessor(tmp_path, audit=audit)
    frame = accessor.read()
    assert len(frame) == 1
    assert audit, "the loader recorded its file opens"
    assert all("reserve_sealed" not in path.parts for path in audit)
    assert all(path == tmp_path / "events.parquet" for path in audit)


# ---------------------------------------------------------------------------
# Determinism and schema
# ---------------------------------------------------------------------------


def test_output_schema_and_determinism(calendar) -> None:
    rows = _normalize(
        [
            _filing(accessionNumber="A-2"),
            _filing(accessionNumber="A-1"),
            _filing(accessionNumber="A-3"),
        ],
        calendar,
    )
    frame = E.events_frame(rows)
    assert tuple(frame.columns) == E.EVENT_COLUMNS
    assert list(frame["accession"]) == ["A-1", "A-2", "A-3"]

    reversed_frame = E.events_frame(list(reversed(rows)))
    assert frame.equals(reversed_frame)


def test_parquet_bytes_are_deterministic(calendar, tmp_path: Path) -> None:
    rows = _normalize(
        [_filing(accessionNumber="A-2"), _filing(accessionNumber="A-1")],
        calendar,
    )
    first = E.write_events_parquet(tmp_path / "one.parquet", rows)
    second = E.write_events_parquet(tmp_path / "two.parquet", list(reversed(rows)))
    assert first.read_bytes() == second.read_bytes()


# ---------------------------------------------------------------------------
# Mutation fixture contract
# ---------------------------------------------------------------------------


def test_mutation_fixture_contract() -> None:
    mutations = json.loads(MUTATIONS.read_text(encoding="utf-8"))
    assert [mutation["id"] for mutation in mutations] == [
        "CONTROL",
        "E3-M1",
        "E3-M2",
        "E3-M3",
        "E3-M4",
        "E3-M5",
        "E3-M6",
        "E3-M7",
        "E3-M8",
        "E3-M9",
    ]
    source = (REPO_ROOT / "smart_beta/dataset_v1/edgar_events.py").read_text(
        encoding="utf-8"
    )
    this_module = sys.modules[__name__]
    for mutation in mutations:
        assert mutation["file"] == "smart_beta/dataset_v1/edgar_events.py"
        assert mutation["description"]
        module_name, _, test_name = mutation["test"].partition("::")
        assert module_name == "tests/test_edgar_events.py"
        assert hasattr(this_module, test_name), mutation["id"]
        if mutation["id"] == "CONTROL":
            assert mutation["edits"] == []
            continue
        assert mutation["edits"], mutation["id"]
        for edit in mutation["edits"]:
            assert edit["old"] and edit["new"] is not None
            assert source.count(edit["old"]) == 1, (mutation["id"], edit["old"])
            assert edit["old"] != edit["new"], mutation["id"]
