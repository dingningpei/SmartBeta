"""Tests for ``TiingoPITSource`` composition (Phase 4B, task P4B-8).

Every test here is offline. The source is constructed with a
:class:`TiingoClient` over recorded fixtures; an autouse tripwire replaces
``urllib.request.urlopen`` so an accidental live call fails loudly. No test
re-verifies Wave 1/2 mapping correctness -- these tests only prove that
``TiingoPITSource`` composes those modules correctly:

1. it is a :class:`PITDataSource`;
2. each method delegates to the matching Wave 1/2 function, once per ticker;
3. real AAPL fixture data flows end-to-end through raw returns and
   fundamentals, matching a direct Wave 2 call;
4. all six panel outputs conform to their canonical schemas (and
   ``trading_calendar`` returns a real :class:`TradingCalendar`);
5. construction fetches nothing;
6. a two-ticker universe concatenates rows for both tickers.

Fixture note: ``get_fundamentals`` calls two client methods that share one
URL path, which the path-keyed ``replay_transport`` cannot distinguish. The
client helper below wraps ``replay_transport`` with a param-aware branch for
that one endpoint. This is a test-harness accommodation for a real Wave 1
replay limitation, reported in ``tests/fixtures/tiingo/source/README.md``.
"""

from __future__ import annotations

import json
import urllib.request
from datetime import date
from pathlib import Path
from unittest import mock

import pandas as pd
import pytest

from smart_beta.pit.calendar import TradingCalendar
from smart_beta.pit.schema import (
    ADJUSTMENT_FACTOR_COL,
    CORPORATE_ACTIONS_SCHEMA,
    DATE_COL,
    EFFECTIVE_DATE_COL,
    FIELD_COL,
    FLOAT_MARKET_CAP_COL,
    FUNDAMENTALS_FACT_SCHEMA,
    IS_RESTATEMENT_COL,
    IS_SUPERSEDED_COL,
    KNOWLEDGE_DATE_COL,
    PIT_LISTING_INFO_SCHEMA,
    PIT_MARKET_CAP_SCHEMA,
    PIT_RAW_RETURN_PANEL_SCHEMA,
    PIT_TRADING_STATUS_SCHEMA,
    RAW_RETURN_COL,
    REPORT_PERIOD_END_COL,
    STOCK_COL,
    TOTAL_MARKET_CAP_COL,
    VALUE_COL,
    validate_panel,
)
from smart_beta.pit.source import PITDataSource
from smart_beta.vendors.tiingo import source as source_mod
from smart_beta.vendors.tiingo.calendar_source import NYSE_HOLIDAYS
from smart_beta.vendors.tiingo.client import TiingoClient, replay_transport
from smart_beta.vendors.tiingo.corporate_actions import map_eod_to_corporate_actions
from smart_beta.vendors.tiingo.fundamentals import map_asreported_to_fundamentals
from smart_beta.vendors.tiingo.identifiers import ResolvedIdentifier
from smart_beta.vendors.tiingo.returns_and_market_cap import map_eod_to_raw_returns
from smart_beta.vendors.tiingo.source import TiingoPITSource

_FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "tiingo" / "source"
_MANIFEST: dict = json.loads(
    (_FIXTURE_DIR / "manifest.json").read_text(encoding="utf-8")
)["recordings"]

_AAPL_META = "aapl_meta.json"
_AAPL_EOD = "aapl_eod_prices_2020-08-20_2020-09-05.json"
_AAPL_ASREPORTED = "aapl_fundamentals_asreported.json"
_AAPL_NORMALIZED = "aapl_fundamentals_normalized.json"
_AAPL_DAILY = "aapl_fundamentals_daily_2024-01-02_2024-01-05.json"
_TWTR_META = "twtr_meta.json"
_TWTR_EOD = "twtr_eod_prices_2022-10-20_2022-10-28.json"

_STATEMENTS_PATH = "/tiingo/fundamentals/AAPL/statements"
_INGESTED_AT_COL = "_ingested_at"


# ---------------------------------------------------------------------------
# Fixture loading / client construction
# ---------------------------------------------------------------------------
def _body(filename: str) -> object:
    return json.loads((_FIXTURE_DIR / filename).read_text(encoding="utf-8"))


def _path_recordings() -> dict[str, tuple[int, object]]:
    """``replay_transport``-style recordings for every path except the
    statements endpoint (whose two variants share one path)."""
    recordings: dict[str, tuple[int, object]] = {}
    for filename, entry in _MANIFEST.items():
        if filename in (_AAPL_ASREPORTED, _AAPL_NORMALIZED):
            continue
        recordings[entry["url_path"]] = (entry["status_code"], _body(filename))
    return recordings


def _make_client() -> TiingoClient:
    """A fixture-fed client; statements dispatch on the ``asReported`` param."""
    base = replay_transport(_path_recordings())

    def transport(path: str, params) -> tuple[int, object]:
        if path == _STATEMENTS_PATH:
            body = (
                _body(_AAPL_ASREPORTED)
                if params.get("asReported") == "true"
                else _body(_AAPL_NORMALIZED)
            )
            return 200, body
        return base(path, params)

    return TiingoClient(transport=transport)


def _source(tickers: list[str] | tuple[str, ...] = ("AAPL",)) -> TiingoPITSource:
    return TiingoPITSource(list(tickers), client=_make_client())


class _TripwireTransport:
    """Transport that fails if anything calls it."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, path: str, params) -> tuple[int, object]:
        self.calls.append((path, dict(params)))
        raise AssertionError(f"unexpected transport call to {path!r}")


@pytest.fixture(autouse=True)
def _forbid_live_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*args: object, **kwargs: object) -> object:
        raise AssertionError(
            "Test attempted a live network call; inject a transport instead."
        )

    monkeypatch.setattr(urllib.request, "urlopen", _boom)


# ---------------------------------------------------------------------------
# Empty frames for mapper mocks (delegation tests only)
# ---------------------------------------------------------------------------
def _empty_frame(*columns: str) -> pd.DataFrame:
    data: dict[str, pd.Series] = {}
    for column in columns:
        if column in (DATE_COL, EFFECTIVE_DATE_COL, KNOWLEDGE_DATE_COL,
                      REPORT_PERIOD_END_COL):
            data[column] = pd.Series([], dtype="datetime64[ns]")
        elif column == STOCK_COL:
            data[column] = pd.Series([], dtype="string")
        elif column in (ADJUSTMENT_FACTOR_COL, FLOAT_MARKET_CAP_COL,
                        TOTAL_MARKET_CAP_COL, RAW_RETURN_COL, VALUE_COL):
            data[column] = pd.Series([], dtype="float64")
        elif column in (IS_RESTATEMENT_COL, IS_SUPERSEDED_COL):
            data[column] = pd.Series([], dtype="bool")
        else:
            data[column] = pd.Series([], dtype="string")
    return pd.DataFrame(data)


def _strip_ingested(frame: pd.DataFrame) -> pd.DataFrame:
    return frame.drop(columns=[_INGESTED_AT_COL]).reset_index(drop=True)


# ---------------------------------------------------------------------------
# 1. ABC conformance
# ---------------------------------------------------------------------------
def test_source_is_a_pit_data_source() -> None:
    assert isinstance(_source(), PITDataSource)


# ---------------------------------------------------------------------------
# 5. Construction fetches nothing
# ---------------------------------------------------------------------------
def test_construction_fetches_nothing() -> None:
    transport = _TripwireTransport()
    client = TiingoClient(transport=transport)
    # Must not raise: construction is lazy and touches no endpoint.
    TiingoPITSource(["AAPL", "TWTR"], client=client)
    assert transport.calls == []


def test_construction_does_not_resolve_or_map() -> None:
    with mock.patch.object(
        source_mod, "resolve_stock_id", side_effect=AssertionError("resolved")
    ), mock.patch.object(
        source_mod, "map_eod_to_raw_returns", side_effect=AssertionError("mapped")
    ):
        _source(["AAPL"])  # no raise


# ---------------------------------------------------------------------------
# 2. Delegation, per method
# ---------------------------------------------------------------------------
def test_trading_calendar_delegates_to_build_nyse_calendar() -> None:
    source = _source(["AAPL"])
    sentinel = TradingCalendar([pd.Timestamp("2020-01-02")])
    with mock.patch.object(
        source_mod, "build_nyse_calendar", return_value=sentinel
    ) as build:
        result = source.trading_calendar()

    assert result is sentinel
    assert build.call_count == 1
    assert build.call_args.args == (
        date(min(NYSE_HOLIDAYS), 1, 1),
        date(max(NYSE_HOLIDAYS), 12, 31),
    )


def test_get_raw_returns_delegates_once_per_ticker() -> None:
    source = _source(["AAPL", "TWTR"])
    empty = _empty_frame(DATE_COL, STOCK_COL, RAW_RETURN_COL)
    with mock.patch.object(
        source_mod, "map_eod_to_raw_returns", return_value=empty
    ) as mapper:
        source.get_raw_returns("2020-08-21", "2020-09-05")

    assert mapper.call_count == 2
    assert [call.args[1] for call in mapper.call_args_list] == ["AAPL", "TWTR"]
    for call in mapper.call_args_list:
        assert isinstance(call.args[0], list)
        assert call.kwargs["source_endpoint"] == "get_eod_prices"


def test_get_market_cap_delegates_to_map_eod_to_market_cap() -> None:
    source = _source(["AAPL"])
    empty = _empty_frame(
        DATE_COL, STOCK_COL, FLOAT_MARKET_CAP_COL, TOTAL_MARKET_CAP_COL
    )
    with mock.patch.object(
        source_mod, "map_eod_to_market_cap", return_value=empty
    ) as mapper:
        source.get_market_cap("2024-01-02", "2024-01-05")

    assert mapper.call_count == 1
    assert mapper.call_args.args[1] == "AAPL"
    assert mapper.call_args.kwargs["source_endpoint"] == "get_fundamentals_daily"


def test_get_trading_status_delegates_once_per_ticker() -> None:
    source = _source(["AAPL", "TWTR"])
    empty = _empty_frame(DATE_COL, STOCK_COL)
    with mock.patch.object(
        source_mod, "map_eod_to_trading_status", return_value=empty
    ) as mapper:
        source.get_trading_status("2020-08-20", "2020-09-05")

    assert mapper.call_count == 2
    assert [call.args[1] for call in mapper.call_args_list] == ["AAPL", "TWTR"]
    assert all(
        call.kwargs["source_endpoint"] == "get_eod_prices"
        for call in mapper.call_args_list
    )


def test_get_corporate_actions_delegates_once_per_ticker() -> None:
    source = _source(["AAPL", "TWTR"])
    empty = _empty_frame(
        STOCK_COL,
        EFFECTIVE_DATE_COL,
        "action_type",
        KNOWLEDGE_DATE_COL,
        ADJUSTMENT_FACTOR_COL,
        IS_SUPERSEDED_COL,
    )
    with mock.patch.object(
        source_mod, "map_eod_to_corporate_actions", return_value=empty
    ) as mapper:
        source.get_corporate_actions("2020-08-20", "2020-09-05")

    assert mapper.call_count == 2
    assert [call.args[1] for call in mapper.call_args_list] == ["AAPL", "TWTR"]
    assert all(
        call.kwargs["source_endpoint"] == "get_eod_prices"
        for call in mapper.call_args_list
    )


def test_get_fundamentals_delegates_to_map_asreported_to_fundamentals() -> None:
    source = _source(["AAPL"])
    empty = _empty_frame(
        STOCK_COL,
        REPORT_PERIOD_END_COL,
        FIELD_COL,
        KNOWLEDGE_DATE_COL,
        VALUE_COL,
        IS_RESTATEMENT_COL,
    )
    fields = ("revenue", "netinc")
    with mock.patch.object(
        source_mod, "map_asreported_to_fundamentals", return_value=empty
    ) as mapper:
        source.get_fundamentals("2026-01-01", "2026-12-31", fields)

    assert mapper.call_count == 1
    assert mapper.call_args.args[0] == "AAPL"
    assert mapper.call_args.args[3] == fields
    # Both statement responses really reached the mapper.
    assert isinstance(mapper.call_args.args[1], list)
    assert mapper.call_args.args[1]
    assert isinstance(mapper.call_args.args[2], list)
    assert mapper.call_args.args[2]


def test_get_listing_info_delegates_once_per_ticker() -> None:
    source = _source(["AAPL", "TWTR"])
    empty = _empty_frame(STOCK_COL, "list_date", "delist_date")
    with mock.patch.object(
        source_mod, "map_meta_to_listing_info", return_value=empty
    ) as mapper:
        source.get_listing_info()

    assert mapper.call_count == 2
    assert [call.args[2] for call in mapper.call_args_list] == ["AAPL", "TWTR"]
    # Each call gets the matching ticker's real metadata + EOD history.
    for call in mapper.call_args_list:
        assert isinstance(call.args[0], dict)
        assert isinstance(call.args[1], list)


def test_resolve_stock_id_supplies_the_mapper_stock_id() -> None:
    source = _source(["AAPL"])
    empty = _empty_frame(DATE_COL, STOCK_COL, RAW_RETURN_COL)
    resolved = ResolvedIdentifier(
        stock_id="US-PERMANENT", is_permanent=True, source_field="permaTicker"
    )
    with mock.patch.object(
        source_mod, "resolve_stock_id", return_value=resolved
    ) as resolver, mock.patch.object(
        source_mod, "map_eod_to_raw_returns", return_value=empty
    ) as mapper:
        source.get_raw_returns("2020-08-21", "2020-09-05")

    resolver.assert_called_once()
    assert mapper.call_args.args[1] == "US-PERMANENT"


# ---------------------------------------------------------------------------
# 3. End-to-end AAPL scenarios
# ---------------------------------------------------------------------------
def test_end_to_end_raw_returns_matches_direct_mapper() -> None:
    source = _source(["AAPL"])
    frame = source.get_raw_returns("2020-08-21", "2020-09-05")
    assert not frame.empty

    direct = map_eod_to_raw_returns(
        _body(_AAPL_EOD), "AAPL", source_endpoint="get_eod_prices"
    )
    start = pd.Timestamp("2020-08-21")
    end = pd.Timestamp("2020-09-05")
    direct = direct.loc[
        (direct[DATE_COL] >= start) & (direct[DATE_COL] <= end)
    ].reset_index(drop=True)

    pd.testing.assert_frame_equal(
        _strip_ingested(frame), _strip_ingested(direct)
    )


def test_end_to_end_fundamentals_matches_direct_mapper() -> None:
    source = _source(["AAPL"])
    fields = ("revenue", "netinc", "totalAssets")
    frame = source.get_fundamentals("2026-01-01", "2026-12-31", fields)
    assert not frame.empty

    direct = map_asreported_to_fundamentals(
        "AAPL",
        _body(_AAPL_ASREPORTED),
        _body(_AAPL_NORMALIZED),
        fields,
    )
    start = pd.Timestamp("2026-01-01")
    end = pd.Timestamp("2026-12-31")
    direct = direct.loc[
        (direct[REPORT_PERIOD_END_COL] >= start)
        & (direct[REPORT_PERIOD_END_COL] <= end)
    ].reset_index(drop=True)

    pd.testing.assert_frame_equal(
        _strip_ingested(frame), _strip_ingested(direct)
    )
    # The non-calendar fiscal period end really survived composition.
    assert (frame[REPORT_PERIOD_END_COL] == pd.Timestamp("2026-06-27")).any()
    assert (frame[KNOWLEDGE_DATE_COL] == pd.Timestamp("2026-07-31")).any()


# ---------------------------------------------------------------------------
# 4. Schema conformance for all panel methods
# ---------------------------------------------------------------------------
def test_schema_conformance_for_every_panel_method() -> None:
    source = _source(["AAPL"])

    raw = source.get_raw_returns("2020-08-21", "2020-09-05")
    validate_panel(raw, PIT_RAW_RETURN_PANEL_SCHEMA, name="raw_returns")

    actions = source.get_corporate_actions("2020-08-20", "2020-09-05")
    validate_panel(actions, CORPORATE_ACTIONS_SCHEMA, name="corporate_actions")

    market_cap = source.get_market_cap("2024-01-02", "2024-01-05")
    validate_panel(market_cap, PIT_MARKET_CAP_SCHEMA, name="market_cap")

    fundamentals = source.get_fundamentals(
        "2026-01-01", "2026-12-31", ("revenue", "netinc")
    )
    validate_panel(fundamentals, FUNDAMENTALS_FACT_SCHEMA, name="fundamentals")

    status = source.get_trading_status("2020-08-20", "2020-09-05")
    validate_panel(status, PIT_TRADING_STATUS_SCHEMA, name="trading_status")

    listing = source.get_listing_info()
    validate_panel(listing, PIT_LISTING_INFO_SCHEMA, name="listing_info")

    assert isinstance(source.trading_calendar(), TradingCalendar)

    # Real data flowed through every panel method (not just empty frames).
    for name, frame in (
        ("raw_returns", raw),
        ("corporate_actions", actions),
        ("market_cap", market_cap),
        ("fundamentals", fundamentals),
        ("trading_status", status),
        ("listing_info", listing),
    ):
        assert not frame.empty, f"{name} unexpectedly empty"


def test_corporate_actions_surfaces_the_aapl_split() -> None:
    actions = _source(["AAPL"]).get_corporate_actions(
        "2020-08-20", "2020-09-05"
    )
    splits = actions.loc[actions["action_type"] == "split"]
    assert not splits.empty
    assert splits.iloc[0][EFFECTIVE_DATE_COL] == pd.Timestamp("2020-08-31")
    assert splits.iloc[0][ADJUSTMENT_FACTOR_COL] == 4.0
    direct = map_eod_to_corporate_actions(
        _body(_AAPL_EOD), "AAPL", source_endpoint="get_eod_prices"
    )
    assert len(actions) == len(direct)


def test_trading_status_uses_the_zero_volume_flag_column() -> None:
    status = _source(["AAPL"]).get_trading_status("2020-08-20", "2020-09-05")
    assert "is_zero_volume" in status.columns
    assert status["is_zero_volume"].dtype == bool


def test_listing_info_reports_aapl_list_date_and_no_delist() -> None:
    listing = _source(["AAPL"]).get_listing_info()
    assert len(listing) == 1
    assert listing.iloc[0][STOCK_COL] == "AAPL"
    assert listing.iloc[0]["list_date"] == pd.Timestamp("1980-12-12")
    assert pd.isna(listing.iloc[0]["delist_date"])


# ---------------------------------------------------------------------------
# 6. Multi-ticker concatenation
# ---------------------------------------------------------------------------
def test_multi_ticker_raw_returns_concatenates_both_tickers() -> None:
    source = _source(["AAPL", "TWTR"])
    frame = source.get_raw_returns("2020-01-01", "2022-12-31")

    validate_panel(frame, PIT_RAW_RETURN_PANEL_SCHEMA, name="raw_returns")
    assert set(frame[STOCK_COL].unique()) == {"AAPL", "TWTR"}
    assert (frame[STOCK_COL] == "AAPL").any()
    assert (frame[STOCK_COL] == "TWTR").any()


def test_multi_ticker_listing_info_concatenates_both_tickers() -> None:
    source = _source(["AAPL", "TWTR"])
    frame = source.get_listing_info()

    validate_panel(frame, PIT_LISTING_INFO_SCHEMA, name="listing_info")
    assert set(frame[STOCK_COL]) == {"AAPL", "TWTR"}


# ---------------------------------------------------------------------------
# Documented fetch-window behavior (lookback, range filtering)
# ---------------------------------------------------------------------------
def test_raw_returns_fetches_a_lookback_before_start() -> None:
    client = _make_client()
    original = client.get_eod_prices
    calls: list[tuple[str, str, str]] = []

    def spy(ticker: str, start_date: str, end_date: str) -> list[dict]:
        calls.append((ticker, start_date, end_date))
        return original(ticker, start_date, end_date)

    client.get_eod_prices = spy  # type: ignore[method-assign]
    TiingoPITSource(["AAPL"], client=client).get_raw_returns(
        "2020-08-21", "2020-09-05"
    )

    assert len(calls) == 1
    ticker, start_date, end_date = calls[0]
    assert ticker == "AAPL"
    assert start_date < "2020-08-21"
    assert end_date == "2020-09-05"


def test_panel_outputs_are_filtered_to_the_requested_range() -> None:
    source = _source(["AAPL"])

    raw = source.get_raw_returns("2020-08-25", "2020-08-28")
    assert raw[DATE_COL].min() >= pd.Timestamp("2020-08-25")
    assert raw[DATE_COL].max() <= pd.Timestamp("2020-08-28")

    actions = source.get_corporate_actions("2020-08-25", "2020-08-28")
    assert actions.empty  # the split is on 2020-08-31, outside the range
    validate_panel(actions, CORPORATE_ACTIONS_SCHEMA, name="corporate_actions")

    # Range filters on report_period_end (Q3 ends 2026-06-27), not on the
    # as-reported knowledge_date (2026-07-31, deliberately outside the range).
    fundamentals = source.get_fundamentals("2026-06-01", "2026-06-30", ("revenue",))
    assert not fundamentals.empty
    assert (fundamentals[REPORT_PERIOD_END_COL] == pd.Timestamp("2026-06-27")).all()
    validate_panel(fundamentals, FUNDAMENTALS_FACT_SCHEMA, name="fundamentals")
