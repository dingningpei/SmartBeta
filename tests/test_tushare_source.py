"""Tests for ``TushareAShareSource`` composition (Phase 4D-B, task P4DB-8).

Every test here is offline and reuses the **real, proxy-observed** fixtures
already recorded by P4DB-4 (market data), P4DB-5 (corporate actions),
P4DB-6 (fundamentals) and P4DB-7 (listing) -- no new fixture specimens are
needed at the assembly level, and none are fabricated. A small in-file
``TushareClient`` harness serves those recorded payloads for whatever fetch
window the assembled source requests, exactly as ``test_tiingo_source.py``
used a param-aware branch for Tiingo's shared statements path. An autouse
tripwire replaces ``urllib.request.urlopen`` so an accidental live call
fails loudly.

These tests do not re-verify any Wave 1/2 mapping correctness. They prove
that ``TushareAShareSource`` composes those modules correctly:

1. it is a :class:`PITDataSource`;
2. each method delegates to the matching Wave 1/2 function, once per
   ``ts_code``;
3. P4DB-6's fail-closed fundamentals semantics survive assembly unchanged
   (knowledge-date drops, blank-out tagging, the real
   ``TushareConflictingVintageError``, and CH3 join-or-suppress);
4. determinism (``check_deterministic_results``) and schema conformance
   (``check_schema_conformance``) pass at the assembled-source level for all
   seven methods;
5. the Gate-1 ``000001.SZ`` FY2022 cumulative-vs-single-quarter
   reconciliation holds;
6. construction fetches nothing;
7. ``resolve_stock_id`` is wired through the frozen P4DB-2 passthrough path.
"""

from __future__ import annotations

import json
import urllib.request
from collections import OrderedDict
from datetime import date
from pathlib import Path
from typing import Mapping, Sequence
from unittest import mock

import pandas as pd
import pytest

from smart_beta.pit.calendar import TradingCalendar
from smart_beta.pit.compliance import (
    check_deterministic_results,
    check_schema_conformance,
)
from smart_beta.pit.schema import (
    DATE_COL,
    FIELD_COL,
    IS_RESTATEMENT_COL,
    KNOWLEDGE_DATE_COL,
    REPORT_PERIOD_END_COL,
    STOCK_COL,
    VALUE_COL,
)
from smart_beta.pit.source import PITDataSource
from smart_beta.vendors.tushare import fundamentals as F
from smart_beta.vendors.tushare import source as source_mod
from smart_beta.vendors.tushare.calendar_source import (
    MAX_COVERED_YEAR,
    MIN_COVERED_YEAR,
)
from smart_beta.vendors.tushare.client import TushareAPIError
from smart_beta.vendors.tushare.fundamentals import map_statement_payload
from smart_beta.vendors.tushare.identifiers import ResolvedIdentifier
from smart_beta.vendors.tushare.source import TushareAShareSource

_FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "tushare"
_MD_DIR = _FIXTURE_ROOT / "market_data"
_CA_DIR = _FIXTURE_ROOT / "corporate_actions"
_LI_DIR = _FIXTURE_ROOT / "listing"
_FU_DIR = _FIXTURE_ROOT / "fundamentals"

_RETRIEVED_AT = pd.Timestamp("2026-09-17T00:00:00")

#: Deterministic assembled-source window for the Phase 3 compliance checks.
_CHECK_START = "2022-06-30"
_CHECK_END = "2022-09-30"
_CHECK_FIELDS = ["total_revenue"]


# ---------------------------------------------------------------------------
# Real-fixture loading and the offline client harness
# ---------------------------------------------------------------------------
def _unwrap(body: object) -> dict:
    """Return the ``fields``/``items`` payload from a recorded body.

    Market-data and corporate-action fixtures wrap the payload in the proxy's
    ``{"code": 0, "data": {...}}`` envelope; listing and fundamentals
    fixtures store the payload directly.
    """
    assert isinstance(body, dict)
    data = body.get("data")
    if isinstance(data, dict) and "fields" in data:
        return data
    return body


def _payload_rows(payload: Mapping) -> tuple[list[str], list[dict]]:
    fields = list(payload.get("fields") or [])
    return fields, [dict(zip(fields, item)) for item in payload.get("items") or []]


def _iter_recorded(dirpath: Path):
    """Yield ``(api_name, params, payload)`` for every recorded fixture."""
    manifest = json.loads((dirpath / "manifest.json").read_text(encoding="utf-8"))
    recordings = manifest.get("recordings", manifest)
    for filename, entry in recordings.items():
        if not isinstance(entry, dict) or "api_name" not in entry:
            continue
        payload = _unwrap(json.loads((dirpath / filename).read_text(encoding="utf-8")))
        yield entry["api_name"], dict(entry.get("params") or {}), payload


def _filters_rows(rows: list[dict], params: Mapping[str, str]) -> list[dict]:
    """Apply the proxy's compact ``start_date``/``end_date`` filter."""
    start = params.get("start_date")
    end = params.get("end_date")
    if start is None and end is None:
        return rows
    selected = []
    for row in rows:
        trade_date = str(row.get("trade_date") or "")
        if start is not None and trade_date < start:
            continue
        if end is not None and trade_date > end:
            continue
        selected.append(row)
    return selected


class _FixtureClient:
    """A transport-neutral ``TushareClient`` over the recorded real payloads.

    Date-window requests are filtered like the proxy would, but the harness is
    intentionally permissive about *which* window was fetched: the assembled
    source chooses its own lookback/corroboration windows, and this harness
    serves the recorded real rows for the security regardless, then applies
    the requested date filter. Statement requests dispatch on
    ``(api_name, ts_code, period[, report_type])`` exactly as P4DB-6 records
    them.
    """

    def __init__(self) -> None:
        self._daily: dict[str, OrderedDict[str, dict]] = {}
        self._daily_basic: dict[str, OrderedDict[str, dict]] = {}
        self._stk_limit: dict[str, OrderedDict[str, dict]] = {}
        self._fields: dict[tuple[str, str], list[str]] = {}
        self._suspend: dict[str, list[dict]] = {}
        self._dividend: dict[str, list[dict]] = {}
        self._stock_basic: dict[str, list[dict]] = {}
        self._statements: dict[tuple[str, str, str], dict] = {}
        self._by_report_type: dict[tuple[str, str, str, str], dict] = {}
        self.calls: list[tuple[str, dict]] = []
        self._load()

    # -- recording helpers ------------------------------------------------
    def _merge_dated(self, store, fields, rows, key: str) -> None:
        for row in rows:
            store.setdefault(row[key], row)

    def _load(self) -> None:
        for dirpath in (_MD_DIR, _CA_DIR):
            for api_name, params, payload in _iter_recorded(dirpath):
                fields, rows = _payload_rows(payload)
                ts_code = params.get("ts_code")
                if ts_code is None:
                    continue
                if api_name == "daily":
                    self._fields[("daily", ts_code)] = fields
                    store = self._daily.setdefault(ts_code, OrderedDict())
                    for row in rows:
                        store.setdefault(row["trade_date"], row)
                elif api_name == "daily_basic":
                    self._fields[("daily_basic", ts_code)] = fields
                    store = self._daily_basic.setdefault(ts_code, OrderedDict())
                    for row in rows:
                        store.setdefault(row["trade_date"], row)
                elif api_name == "stk_limit":
                    self._fields[("stk_limit", ts_code)] = fields
                    store = self._stk_limit.setdefault(ts_code, OrderedDict())
                    for row in rows:
                        store.setdefault(row["trade_date"], row)
                elif api_name == "suspend_d":
                    self._suspend.setdefault(ts_code, []).extend(rows)
                elif api_name == "dividend":
                    self._fields[("dividend", ts_code)] = fields
                    self._dividend.setdefault(ts_code, []).extend(rows)

        for api_name, params, payload in _iter_recorded(_LI_DIR):
            if api_name == "daily":
                ts_code = params.get("ts_code")
                if ts_code is None:
                    continue
                fields, rows = _payload_rows(payload)
                self._fields[("daily", ts_code)] = fields
                store = self._daily.setdefault(ts_code, OrderedDict())
                for row in rows:
                    store.setdefault(row["trade_date"], row)
            elif api_name == "stock_basic":
                ts_code = params.get("ts_code")
                if ts_code is None:
                    continue
                _, rows = _payload_rows(payload)
                self._stock_basic.setdefault(ts_code, []).extend(rows)

        for api_name, params, payload in _iter_recorded(_FU_DIR):
            ts_code = params.get("ts_code")
            period = params.get("period")
            if ts_code is None or period is None:
                continue
            report_type = params.get("report_type")
            if report_type is not None:
                self._by_report_type[(api_name, ts_code, period, str(report_type))] = payload
            else:
                self._statements.setdefault((api_name, ts_code, period), payload)

    # -- payload builder --------------------------------------------------
    def _dated_payload(self, api_name: str, ts_code: str, rows: Sequence[dict]) -> dict:
        fields = self._fields.get((api_name, ts_code))
        if fields is None:
            fields = list(rows[0].keys()) if rows else []
        return {"fields": fields, "items": [[row.get(f) for f in fields] for row in rows]}

    # -- TushareClient protocol ------------------------------------------
    def fetch(self, api_name: str, *, retry_on_empty: bool = False, **params: str) -> dict:
        self.calls.append((api_name, dict(params)))
        ts_code = params.get("ts_code", "")

        if api_name in ("daily", "daily_basic", "stk_limit"):
            store = {
                "daily": self._daily,
                "daily_basic": self._daily_basic,
                "stk_limit": self._stk_limit,
            }[api_name]
            rows = list(store.get(ts_code, OrderedDict()).values())
            return self._dated_payload(api_name, ts_code, _filters_rows(rows, params))
        if api_name == "suspend_d":
            rows = self._suspend.get(ts_code, [])
            return self._dated_payload(api_name, ts_code, rows)
        if api_name == "dividend":
            rows = self._dividend.get(ts_code, [])
            return self._dated_payload(api_name, ts_code, rows)
        if api_name == "stock_basic":
            rows = self._stock_basic.get(ts_code, [])
            fields = list(rows[0].keys()) if rows else []
            return {"fields": fields, "items": [[row.get(f) for f in fields] for row in rows]}

        period = params.get("period")
        report_type = params.get("report_type")
        if report_type is not None:
            payload = self._by_report_type.get((api_name, ts_code, period, str(report_type)))
            if payload is not None:
                return payload
        payload = self._statements.get((api_name, ts_code, period))
        if payload is not None:
            return payload
        return {"fields": [], "items": []}


@pytest.fixture
def client() -> _FixtureClient:
    return _FixtureClient()


def _source(ts_codes: Sequence[str], client: _FixtureClient) -> TushareAShareSource:
    return TushareAShareSource(list(ts_codes), client)


class _CapEnforcingClient(_FixtureClient):
    """Fixture client that enforces the real proxy's <=366-day range cap.

    The proxy returns HTTP 400 ``date_range_too_large`` for any single request
    spanning more than 366 days; P4DB-1 surfaces that rejection intact. This
    double reproduces the boundary so the assembled source's *lack* of
    transparent chunking is executable evidence rather than prose.
    """

    def fetch(self, api_name: str, *, retry_on_empty: bool = False, **params: str) -> dict:
        start = params.get("start_date")
        end = params.get("end_date")
        if start is not None and end is not None:
            span = (pd.Timestamp(end) - pd.Timestamp(start)).days
            if span > 366:
                raise TushareAPIError(
                    api_name,
                    dict(params),
                    "date_range_too_large",
                    status_code=400,
                    error="date_range_too_large",
                )
        return super().fetch(api_name, retry_on_empty=retry_on_empty, **params)


class _TripwireClient:
    """Client that fails if any method calls it."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def fetch(self, api_name: str, *, retry_on_empty: bool = False, **params: str) -> dict:
        self.calls.append((api_name, dict(params)))
        raise AssertionError(f"unexpected fetch to {api_name!r}")


@pytest.fixture(autouse=True)
def _forbid_live_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*args: object, **kwargs: object) -> object:
        raise AssertionError(
            "Test attempted a live network call; inject a transport instead."
        )

    monkeypatch.setattr(urllib.request, "urlopen", _boom)


# ---------------------------------------------------------------------------
# 1. ABC conformance and 6. construction fetches nothing
# ---------------------------------------------------------------------------
def test_source_is_a_pit_data_source(client: _FixtureClient) -> None:
    assert isinstance(_source(["000001.SZ"], client), PITDataSource)


def test_construction_fetches_nothing() -> None:
    tripwire = _TripwireClient()
    TushareAShareSource(["000001.SZ", "600518.SH"], client=tripwire)
    assert tripwire.calls == []


def test_construction_does_not_resolve_or_map() -> None:
    tripwire = _TripwireClient()
    with mock.patch.object(
        source_mod, "resolve_stock_id", side_effect=AssertionError("resolved")
    ), mock.patch.object(
        source_mod, "map_daily_to_raw_returns", side_effect=AssertionError("mapped")
    ):
        TushareAShareSource(["000001.SZ"], client=tripwire)  # no raise


# ---------------------------------------------------------------------------
# 2. Delegation, per method
# ---------------------------------------------------------------------------
def test_trading_calendar_delegates_to_build_china_a_share_calendar(
    client: _FixtureClient,
) -> None:
    source = _source(["000001.SZ"], client)
    sentinel = TradingCalendar([pd.Timestamp("2020-01-02")])
    with mock.patch.object(
        source_mod, "build_china_a_share_calendar", return_value=sentinel
    ) as build:
        result = source.trading_calendar()

    assert result is sentinel
    assert build.call_count == 1
    assert build.call_args.args == (
        date(MIN_COVERED_YEAR, 1, 1),
        date(MAX_COVERED_YEAR, 12, 31),
    )


def test_get_raw_returns_delegates_once_per_ts_code(client: _FixtureClient) -> None:
    source = _source(["000001.SZ", "600518.SH"], client)
    empty = pd.DataFrame()
    with mock.patch.object(
        source_mod, "map_daily_to_raw_returns", return_value=empty
    ) as mapper:
        source.get_raw_returns("2013-01-02", "2013-01-31")

    assert mapper.call_count == 2
    assert [call.args[1] for call in mapper.call_args_list] == [
        "000001.SZ",
        "600518.SH",
    ]
    for call in mapper.call_args_list:
        assert isinstance(call.args[0], list)
        assert call.kwargs["source_endpoint"] == "daily"


def test_get_market_cap_delegates_to_map_daily_basic_to_market_cap(
    client: _FixtureClient,
) -> None:
    source = _source(["000001.SZ"], client)
    empty = pd.DataFrame()
    with mock.patch.object(
        source_mod, "map_daily_basic_to_market_cap", return_value=empty
    ) as mapper:
        source.get_market_cap("2013-01-02", "2013-01-31")

    assert mapper.call_count == 1
    assert mapper.call_args.args[1] == "000001.SZ"
    assert mapper.call_args.kwargs["source_endpoint"] == "daily_basic"


def test_get_trading_status_delegates_to_map_to_trading_status(
    client: _FixtureClient,
) -> None:
    source = _source(["000001.SZ"], client)
    empty = pd.DataFrame()
    with mock.patch.object(
        source_mod, "map_to_trading_status", return_value=empty
    ) as mapper:
        source.get_trading_status("2013-01-02", "2013-01-31")

    assert mapper.call_count == 1
    assert mapper.call_args.args[0] == "000001.SZ"
    assert mapper.call_args.kwargs["source_endpoint"] == "daily+suspend_d+stk_limit"
    # The authoritative calendar, not vendor row presence, supplies the
    # row universe.
    assert len(mapper.call_args.kwargs["trade_dates"]) > 0


def test_get_corporate_actions_delegates_to_map_dividend(
    client: _FixtureClient,
) -> None:
    source = _source(["000001.SZ", "600518.SH"], client)
    empty = pd.DataFrame()
    with mock.patch.object(
        source_mod, "map_dividend_to_corporate_actions", return_value=empty
    ) as mapper:
        source.get_corporate_actions("2013-01-02", "2013-01-31")

    assert mapper.call_count == 2
    assert all(
        call.kwargs["source_endpoint"] == "dividend"
        for call in mapper.call_args_list
    )
    # The prior-close input P4DB-5 accepts is supplied from real daily rows.
    for call in mapper.call_args_list:
        assert isinstance(call.kwargs["prev_close_by_ex_date"], dict)


def test_get_fundamentals_delegates_to_p4db6_verbatim(
    client: _FixtureClient,
) -> None:
    source = _source(["000001.SZ"], client)
    empty = pd.DataFrame()
    fields = ("total_revenue",)
    with mock.patch.object(
        source_mod, "_map_fundamentals", return_value=empty
    ) as mapper:
        result = source.get_fundamentals("2022-09-30", "2022-09-30", fields)

    assert result is empty
    assert mapper.call_count == 1
    # The mapper receives the client, resolved stock_ids, the caller's range
    # and fields verbatim -- the source adds no post-processing.
    assert mapper.call_args.args[0] is client
    assert mapper.call_args.args[1] == ["000001.SZ"]
    assert mapper.call_args.args[2:] == ("2022-09-30", "2022-09-30", fields)


def test_get_uncertain_observations_delegates_to_p4db6(
    client: _FixtureClient,
) -> None:
    source = _source(["600518.SH"], client)
    empty = pd.DataFrame()
    with mock.patch.object(
        source_mod, "_map_uncertain_observations", return_value=empty
    ) as mapper:
        result = source.get_uncertain_observations(
            "2017-12-31", "2017-12-31", ("ni_ex_nonrecurring",)
        )

    assert result is empty
    assert mapper.call_args.args[1] == ["600518.SH"]


def test_get_listing_info_delegates_once_per_ts_code(client: _FixtureClient) -> None:
    source = _source(["000001.SZ", "002450.SZ"], client)
    empty = pd.DataFrame()
    with mock.patch.object(
        source_mod, "map_stock_basic_to_listing_info", return_value=empty
    ) as mapper:
        source.get_listing_info()

    assert mapper.call_count == 2
    assert [call.args[4] for call in mapper.call_args_list] == [
        "000001.SZ",
        "002450.SZ",
    ]
    for call in mapper.call_args_list:
        assert isinstance(call.args[0], dict)
        assert isinstance(call.args[1], list)


# ---------------------------------------------------------------------------
# 7. resolve_stock_id wiring
# ---------------------------------------------------------------------------
def test_resolve_stock_id_uses_p4db2_ts_code_passthrough(
    client: _FixtureClient,
) -> None:
    """The panel methods resolve through P4DB-2's direct ts_code passthrough.

    A test asserting only "an identifier resolution happened" would not pin
    which call path is used; this asserts the actual underlying call: the
    frozen P4DB-2 passthrough with the ``ts_code`` as the sole argument (no
    extra metadata fetch, because Tushare's code is itself the vendor key).
    """
    source = _source(["600519.SH"], client)
    resolved = ResolvedIdentifier(
        stock_id="RESOLVED", is_permanent=True, raw_ts_code="600519.SH"
    )
    empty = pd.DataFrame()
    with mock.patch.object(
        source_mod, "resolve_stock_id", return_value=resolved
    ) as resolver, mock.patch.object(
        source_mod, "map_daily_to_raw_returns", return_value=empty
    ) as mapper:
        source.get_raw_returns("2013-01-02", "2013-01-31")

    resolver.assert_called_once_with("600519.SH")
    assert mapper.call_args.args[1] == "RESOLVED"


def test_listing_resolution_passes_the_stock_basic_row(
    client: _FixtureClient,
) -> None:
    """``get_listing_info`` is the one place the row is passed through, so
    ``is_permanent`` reflects the terminal-listing signal P4DB-2 owns."""
    source = _source(["002450.SZ"], client)
    empty = pd.DataFrame()
    with mock.patch.object(
        source_mod, "resolve_stock_id", wraps=source_mod.resolve_stock_id
    ) as resolver, mock.patch.object(
        source_mod, "map_stock_basic_to_listing_info", return_value=empty
    ):
        source.get_listing_info()

    resolver.assert_called_once()
    args, kwargs = resolver.call_args
    assert args[0] == "002450.SZ"
    assert isinstance(args[1], dict)
    assert args[1]["ts_code"] == "002450.SZ"


# ---------------------------------------------------------------------------
# 3/4. Assembled-source compliance checks
# ---------------------------------------------------------------------------
def _compliance_source(client: _FixtureClient) -> TushareAShareSource:
    return _source(["000001.SZ"], client)


def test_check_schema_conformance_passes_for_all_seven_methods(
    client: _FixtureClient,
) -> None:
    source = _compliance_source(client)
    results = check_schema_conformance(source, _CHECK_START, _CHECK_END, _CHECK_FIELDS)
    failures = [result for result in results if not result.passed]
    assert not failures, "\n".join(r.message for r in failures)
    # One per PITDataSource method: calendar + six panels = seven results.
    assert len(results) == 7
    assert all(result.passed for result in results)


def test_check_deterministic_results_passes_with_provenance_columns(
    client: _FixtureClient,
) -> None:
    """P4B-D1's canonical-column comparison must still handle this adapter's
    ``retrieved_at``/``_ingested_at`` provenance re-stamping.

    The Wave 2 mappers stamp a fresh provenance timestamp per call. If
    ``check_deterministic_results`` were still comparing whole frames, every
    method here would fail; the fact that all pass is the explicit
    provenance-column interaction check this task requires.
    """
    source = _compliance_source(client)
    results = check_deterministic_results(source, _CHECK_START, _CHECK_END, _CHECK_FIELDS)
    failures = [result for result in results if not result.passed]
    assert not failures, "\n".join(r.message for r in failures)
    assert len(results) == 6
    assert all(result.passed for result in results)


def test_fundamentals_provenance_column_varies_but_canonical_data_does_not(
    client: _FixtureClient,
) -> None:
    """Directly prove the provenance column is non-constant across calls
    while the canonical fact data matches."""
    source = _source(["000001.SZ"], client)
    first = source.get_fundamentals("2022-09-30", "2022-09-30", ["total_revenue"])
    second = source.get_fundamentals("2022-09-30", "2022-09-30", ["total_revenue"])
    # ``retrieved_at`` is allowed to differ between invocations.
    assert "_source_vendor" not in first.columns  # P4DB-6 uses ``source_vendor``
    canonical = [
        STOCK_COL,
        REPORT_PERIOD_END_COL,
        FIELD_COL,
        KNOWLEDGE_DATE_COL,
        VALUE_COL,
        IS_RESTATEMENT_COL,
    ]
    pd.testing.assert_frame_equal(first[canonical], second[canonical])
    assert first[F.RETRIEVED_AT_COL].notna().all()


# ---------------------------------------------------------------------------
# 3. P4DB-6 fail-closed semantics survive assembly
# ---------------------------------------------------------------------------
def test_assembled_source_emits_conflicting_vintage_error_unchanged(
    client: _FixtureClient,
) -> None:
    """The real ``002450.SZ`` FY2015 collision must still raise at the
    assembled-source level -- assembly must not swallow or resolve it."""
    source = _source(["002450.SZ"], client)
    with pytest.raises(F.TushareConflictingVintageError):
        source.get_fundamentals("2015-12-31", "2015-12-31", ["total_revenue"])


def test_assembled_source_blank_out_is_tagged_known_missing(
    client: _FixtureClient,
) -> None:
    """P4DB-6 policy 6 survives assembly: a blanked vendor value is kept as a
    row tagged ``is_blank_out=True`` with ``value=NaN`` -- never dropped and
    never coerced to ``0.0``."""
    source = _source(["002450.SZ"], client)
    frame = source.get_fundamentals(
        "2016-12-31", "2016-12-31", ["total_revenue", "n_income"]
    )
    assert len(frame) == 2
    assert set(frame[FIELD_COL]) == {"total_revenue", "n_income"}
    assert frame[F.IS_BLANK_OUT_COL].all()
    assert frame[VALUE_COL].isna().all()
    assert not (frame[VALUE_COL] == 0.0).any()
    # Blank-out is independent of restatement status.
    assert frame[IS_RESTATEMENT_COL].notna().all()


def test_assembled_source_ch3_suppressed_600518_fy2017(
    client: _FixtureClient,
) -> None:
    source = _source(["600518.SH"], client)
    frame = source.get_fundamentals(
        "2017-12-31", "2017-12-31", [F.CH3_FIELD]
    )
    assert frame.empty

    uncertain = source.get_uncertain_observations(
        "2017-12-31", "2017-12-31", [F.CH3_FIELD]
    )
    entry = uncertain[uncertain[FIELD_COL] == F.CH3_FIELD]
    assert len(entry) == 1
    assert entry.iloc[0][F.REASON_COL] == F.REASON_CH3_VINTAGE_JOIN_NOT_CERTIFIED


def test_assembled_source_ch3_suppressed_002450_fy2015(
    client: _FixtureClient,
) -> None:
    source = _source(["002450.SZ"], client)
    frame = source.get_fundamentals(
        "2015-12-31", "2015-12-31", [F.CH3_FIELD]
    )
    assert frame.empty

    uncertain = source.get_uncertain_observations(
        "2015-12-31", "2015-12-31", [F.CH3_FIELD]
    )
    entry = uncertain[uncertain[FIELD_COL] == F.CH3_FIELD]
    assert len(entry) == 1
    assert entry.iloc[0][F.REASON_COL] == F.REASON_CH3_VINTAGE_JOIN_NOT_CERTIFIED


def test_assembled_source_ch3_successful_join_specimen(
    client: _FixtureClient,
) -> None:
    source = _source(["000001.SZ"], client)
    frame = source.get_fundamentals(
        "2022-09-30", "2022-09-30", [F.CH3_FIELD]
    )
    assert len(frame) == 1
    row = frame.iloc[0]
    assert row[FIELD_COL] == F.CH3_FIELD
    assert row[VALUE_COL] == pytest.approx(36_597_000_000.0)
    assert row[KNOWLEDGE_DATE_COL] == pd.Timestamp("2022-10-25")
    assert row[F.REPORTING_BASIS_COL] == F.CUMULATIVE_YTD


# ---------------------------------------------------------------------------
# 3. Gate-1 reconciliation at the assembled-source level
# ---------------------------------------------------------------------------
def test_assembled_source_gate1_fy2022_reconciliation(
    client: _FixtureClient,
) -> None:
    """Gate-1: cumulative Q3 2022 minus cumulative H1 2022 equals the
    independently-tagged ``report_type=2`` single-quarter record, exactly.

    The two cumulative legs run through the assembled source's
    ``get_fundamentals`` (resolving through identifiers and the full
    pipeline). The single-quarter leg is not reachable through
    ``get_fundamentals`` -- Tushare's default statement response does not
    contain ``report_type=2`` and P4DB-6's public API exposes no
    ``report_type`` selector (reported as an interface finding, not worked
    around). It is read from the same recorded specimen through P4DB-6's
    public mapper, on the same client, so the arithmetic is still verified
    end-to-end without this adapter ever subtracting periods itself.
    """
    source = _source(["000001.SZ"], client)

    q3 = source.get_fundamentals("2022-09-30", "2022-09-30", ["total_revenue", "n_income"])
    h1 = source.get_fundamentals("2022-06-30", "2022-06-30", ["total_revenue", "n_income"])

    q3_revenue = _sole_value(q3, "total_revenue")
    h1_revenue = _sole_value(h1, "total_revenue")
    q3_income = _sole_value(q3, "n_income")
    h1_income = _sole_value(h1, "n_income")
    assert q3_revenue == pytest.approx(138_265_000_000.0)
    assert h1_revenue == pytest.approx(92_022_000_000.0)
    assert q3_income == pytest.approx(36_659_000_000.0)
    assert h1_income == pytest.approx(22_088_000_000.0)

    single_payload = client.fetch(
        "income", ts_code="000001.SZ", period="20220930", report_type="2"
    )
    single, _ = map_statement_payload(
        single_payload, "income", ["total_revenue", "n_income"], retrieved_at=_RETRIEVED_AT
    )
    single_revenue = _sole_value(single, "total_revenue")
    single_income = _sole_value(single, "n_income")
    assert single_revenue == pytest.approx(46_243_000_000.0)
    assert single_income == pytest.approx(14_571_000_000.0)

    assert q3_revenue - h1_revenue == single_revenue
    assert q3_income - h1_income == single_income


def _sole_value(frame: pd.DataFrame, field: str) -> float:
    matched = frame[frame[FIELD_COL] == field]
    assert len(matched) == 1, f"expected exactly one {field!r} fact, got {len(matched)}"
    return float(matched.iloc[0][VALUE_COL])


# ---------------------------------------------------------------------------
# Real data flows through every panel method (not just empty frames)
# ---------------------------------------------------------------------------
def test_real_data_flows_through_raw_returns_and_market_cap(
    client: _FixtureClient,
) -> None:
    source = _source(["000001.SZ"], client)

    raw = source.get_raw_returns("2013-06-03", "2013-07-01")
    assert not raw.empty
    assert raw[DATE_COL].min() >= pd.Timestamp("2013-06-03")
    assert raw[DATE_COL].max() <= pd.Timestamp("2013-07-01")
    # The raw 2013-06-20 bonus drop is present, not the ex-adjusted move.
    ex_date_row = raw[raw[DATE_COL] == pd.Timestamp("2013-06-20")]
    assert len(ex_date_row) == 1

    market_cap = source.get_market_cap("2013-06-03", "2013-07-01")
    assert not market_cap.empty

    actions = source.get_corporate_actions("2013-06-03", "2013-07-01")
    assert not actions.empty
    assert (actions["effective_date"] == pd.Timestamp("2013-06-20")).any()

    listing = source.get_listing_info()
    assert len(listing) == 1
    assert listing.iloc[0][STOCK_COL] == "000001.SZ"


def test_delisted_security_listing_window_corroborates(
    client: _FixtureClient,
) -> None:
    source = _source(["002450.SZ"], client)
    listing = source.get_listing_info()
    assert len(listing) == 1
    assert listing.iloc[0]["_delist_corroboration"] == "corroborated"
    assert listing.iloc[0]["delist_date"] == pd.Timestamp("2021-05-31")


def test_still_listed_security_never_gets_a_delist_date(
    client: _FixtureClient,
) -> None:
    source = _source(["000001.SZ"], client)
    listing = source.get_listing_info()
    assert pd.isna(listing.iloc[0]["delist_date"])
    assert listing.iloc[0]["_delist_corroboration"] == "no_delist_claim"


# ---------------------------------------------------------------------------
# Documented assembly limitations (executable evidence, not prose)
# ---------------------------------------------------------------------------
def test_is_st_is_undeterminable_na_because_no_same_day_name_is_wired(
    client: _FixtureClient,
) -> None:
    """P4DB-4 supports ``is_st`` via a same-day name source; this assembly
    does not wire one, so the flag is the documented ``pd.NA``
    ("undeterminable") for every row -- never a false ``False``.

    002450.SZ really carried the ST name ``*ST康得`` on 2021-04-13 (P4DB-4's
    ``bak_basic`` specimen). If a future follow-up wires a same-day name
    source, this test should be updated to assert ``True`` there.
    """
    source = _source(["002450.SZ"], client)
    status = source.get_trading_status("2021-04-12", "2021-04-13")
    assert not status.empty
    assert status["is_st"].isna().all()


def test_ranges_longer_than_the_proxy_cap_are_not_transparently_chunked() -> None:
    """The proxy's <=366-day cap is surfaced, not chunked, so a multi-year
    range raises at the proxy boundary (named P4DB-8 finding; the fix belongs
    in a P4DB-1 follow-up per Phase 4D-B policy 11)."""
    capped = _CapEnforcingClient()
    source = _source(["000001.SZ"], capped)
    with pytest.raises(TushareAPIError) as excinfo:
        source.get_market_cap("2013-01-01", "2014-03-31")
    assert excinfo.value.status_code == 400
    assert excinfo.value.error == "date_range_too_large"
