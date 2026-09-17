"""Tests for :mod:`smart_beta.vendors.tiingo.returns_and_market_cap`.

Every test here is offline. Mapping functions consume recorded (or
hand-constructed) EOD JSON from
``tests/fixtures/tiingo/returns_market_cap/``. An autouse fixture replaces
``urllib.request.urlopen`` with a tripwire so an accidental live call fails
loudly. One test additionally drives :class:`TiingoClient` through
:func:`replay_transport` to prove the AAPL recording is a real
``get_eod_prices`` body.

Coverage map (required tests from the P4B-4 spec):

1. return computation correctness (AAPL ordinary day, hand-computed)
2. first-row exclusion
3. TWTR zero-volume row present in raw returns AND trading status
4. ``is_zero_volume`` True exactly on zero-volume rows (TWTR terminal +
   isolated mid-history)
5. market cap fails closed with :class:`TiingoMarketCapUnavailableError`
6. schema conformance via ``validate_panel``
7. provenance columns present (and extra columns do not break
   ``validate_panel``)
"""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path

import pandas as pd
import pytest

from smart_beta.pit.schema import (
    DATE_COL,
    PIT_RAW_RETURN_PANEL_SCHEMA,
    PIT_TRADING_STATUS_SCHEMA,
    RAW_RETURN_COL,
    STOCK_COL,
    validate_panel,
)
from smart_beta.vendors.tiingo.client import TiingoClient, replay_transport
from smart_beta.vendors.tiingo.returns_and_market_cap import (
    TiingoMarketCapUnavailableError,
    map_eod_to_market_cap,
    map_eod_to_raw_returns,
    map_eod_to_trading_status,
)

_FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "tiingo" / "returns_market_cap"

_AAPL_EOD = "aapl_eod_prices_2020-08-20_2020-09-05.json"
_TWTR_EOD = "twtr_eod_prices_2022-10-20_2022-10-28.json"
_AAPL_DAILY = "aapl_fundamentals_daily_2024-01-02_2024-01-05.json"
_ISOLATED_EOD = "isolated_zero_volume_eod.json"

_AAPL_ID = "AAPL"
_TWTR_ID = "TWTR"
_ISOLATED_ID = "ILLIQ"

_PROVENANCE_COLS = ("_source_vendor", "_source_endpoint", "_ingested_at")
_CHINA_A_FLAGS = ("is_suspended", "is_limit_up", "is_limit_down", "is_st")
_EOD_MCAP_LIKE = ("marketCap", "market_cap", "shares", "sharesOutstanding", "float")


def _load(filename: str) -> object:
    return json.loads((_FIXTURE_DIR / filename).read_text(encoding="utf-8"))


def _load_eod(filename: str) -> list[dict]:
    body = _load(filename)
    assert isinstance(body, list)
    return body


def _dates(frame: pd.DataFrame) -> set:
    return set(pd.to_datetime(frame[DATE_COL]).dt.date)


def _row_on(frame: pd.DataFrame, day: str) -> pd.Series:
    target = pd.Timestamp(day).date()
    matched = frame[pd.to_datetime(frame[DATE_COL]).dt.date == target]
    assert len(matched) == 1, f"expected one row on {day}, got {len(matched)}"
    return matched.iloc[0]


@pytest.fixture(autouse=True)
def _forbid_live_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*args: object, **kwargs: object) -> object:
        raise AssertionError(
            "Test attempted a live network call; load fixtures from disk."
        )

    monkeypatch.setattr(urllib.request, "urlopen", _boom)


# ---------------------------------------------------------------------------
# 1. Return computation correctness (AAPL ordinary day, hand-computed)
# ---------------------------------------------------------------------------
def test_aapl_ordinary_day_return_is_hand_computed_from_raw_close() -> None:
    rows = _load_eod(_AAPL_EOD)
    # Recorded closes (not adjClose) for 2020-08-20 and 2020-08-21.
    assert rows[0]["date"].startswith("2020-08-20")
    assert rows[1]["date"].startswith("2020-08-21")
    close_0820 = 473.1
    close_0821 = 497.48
    assert rows[0]["close"] == close_0820
    assert rows[1]["close"] == close_0821
    expected = close_0821 / close_0820 - 1.0

    frame = map_eod_to_raw_returns(rows, _AAPL_ID)
    actual = _row_on(frame, "2020-08-21")[RAW_RETURN_COL]
    assert actual == pytest.approx(expected)


def test_aapl_split_day_uses_raw_close_not_adj_close() -> None:
    """2020-08-31 is AAPL's 4-for-1 split; raw close drops, adjClose does not.

    Using adjClose would produce a small positive return; the raw source
    must report the close-to-close drop.
    """
    rows = _load_eod(_AAPL_EOD)
    pre_split = next(r for r in rows if r["date"].startswith("2020-08-28"))
    split_day = next(r for r in rows if r["date"].startswith("2020-08-31"))
    assert split_day["splitFactor"] == 4.0
    close_pre = 499.23
    close_split = 129.04
    assert pre_split["close"] == close_pre
    assert split_day["close"] == close_split
    expected_raw = close_split / close_pre - 1.0
    adj_return = split_day["adjClose"] / pre_split["adjClose"] - 1.0

    frame = map_eod_to_raw_returns(rows, _AAPL_ID)
    actual = _row_on(frame, "2020-08-31")[RAW_RETURN_COL]
    assert actual == pytest.approx(expected_raw)
    assert actual != pytest.approx(adj_return)
    assert actual < 0  # raw close dropped; adjClose rose


# ---------------------------------------------------------------------------
# 2. First-row exclusion
# ---------------------------------------------------------------------------
def test_earliest_date_has_no_raw_return_row() -> None:
    rows = _load_eod(_AAPL_EOD)
    earliest = pd.to_datetime(rows[0]["date"], utc=True).date()
    assert earliest.isoformat() == "2020-08-20"

    frame = map_eod_to_raw_returns(rows, _AAPL_ID)
    assert earliest not in _dates(frame)
    assert len(frame) == len(rows) - 1


def test_single_row_yields_empty_raw_returns() -> None:
    rows = _load_eod(_AAPL_EOD)[:1]
    frame = map_eod_to_raw_returns(rows, _AAPL_ID)
    assert frame.empty
    validate_panel(frame, PIT_RAW_RETURN_PANEL_SCHEMA, name="raw_returns")


# ---------------------------------------------------------------------------
# 3. TWTR zero-volume row is present in returns AND trading status
# ---------------------------------------------------------------------------
def test_twtr_zero_volume_row_is_present_in_returns_and_status() -> None:
    rows = _load_eod(_TWTR_EOD)
    terminal = rows[-1]
    assert terminal["date"].startswith("2022-10-28")
    assert terminal["volume"] == 0
    prev_close = rows[-2]["close"]
    terminal_close = terminal["close"]
    assert prev_close == 53.7
    assert terminal_close == 53.7
    expected_return = terminal_close / prev_close - 1.0  # observed: 0.0

    returns = map_eod_to_raw_returns(rows, _TWTR_ID)
    status = map_eod_to_trading_status(rows, _TWTR_ID)

    assert pd.Timestamp("2022-10-28").date() in _dates(returns)
    assert pd.Timestamp("2022-10-28").date() in _dates(status)
    assert _row_on(returns, "2022-10-28")[RAW_RETURN_COL] == pytest.approx(
        expected_return
    )
    assert bool(_row_on(status, "2022-10-28")["is_zero_volume"]) is True


# ---------------------------------------------------------------------------
# 4. is_zero_volume True exactly on zero-volume rows
# ---------------------------------------------------------------------------
def test_is_zero_volume_true_exactly_on_twtr_terminal_row() -> None:
    rows = _load_eod(_TWTR_EOD)
    status = map_eod_to_trading_status(rows, _TWTR_ID)
    assert len(status) == len(rows)

    zero_volume_dates = {
        pd.to_datetime(row["date"], utc=True).date()
        for row in rows
        if row["volume"] == 0
    }
    assert zero_volume_dates == {pd.Timestamp("2022-10-28").date()}

    for _, row in status.iterrows():
        day = pd.Timestamp(row[DATE_COL]).date()
        assert bool(row["is_zero_volume"]) is (day in zero_volume_dates)


def test_is_zero_volume_true_exactly_on_isolated_mid_history_row() -> None:
    rows = _load_eod(_ISOLATED_EOD)
    status = map_eod_to_trading_status(rows, _ISOLATED_ID)
    assert len(status) == len(rows)

    zero_volume_dates = {
        pd.to_datetime(row["date"], utc=True).date()
        for row in rows
        if row["volume"] == 0
    }
    assert zero_volume_dates == {pd.Timestamp("2021-06-03").date()}
    # Not a terminal row: trading continues after the zero-volume day.
    last_day = pd.to_datetime(rows[-1]["date"], utc=True).date()
    assert last_day not in zero_volume_dates

    for _, row in status.iterrows():
        day = pd.Timestamp(row[DATE_COL]).date()
        assert bool(row["is_zero_volume"]) is (day in zero_volume_dates)


def test_isolated_zero_volume_row_is_preserved_in_raw_returns() -> None:
    rows = _load_eod(_ISOLATED_EOD)
    frame = map_eod_to_raw_returns(rows, _ISOLATED_ID)
    assert pd.Timestamp("2021-06-03").date() in _dates(frame)
    expected = 25.2 / 25.4 - 1.0
    assert _row_on(frame, "2021-06-03")[RAW_RETURN_COL] == pytest.approx(expected)


# ---------------------------------------------------------------------------
# 5. Market cap: fail closed (EOD has no figure; daily metrics unwrapped)
# ---------------------------------------------------------------------------
def test_eod_rows_have_no_market_cap_or_share_count_field() -> None:
    for filename in (_AAPL_EOD, _TWTR_EOD, _ISOLATED_EOD):
        rows = _load_eod(filename)
        keys = set(rows[0])
        for name in _EOD_MCAP_LIKE:
            assert name not in keys, f"{filename} unexpectedly has {name}"


def test_daily_metrics_fixture_has_market_cap_and_no_float_distinct_figure() -> None:
    """Investigation record: marketCap exists on an endpoint the client
    does not wrap, with no distinct float-adjusted figure."""
    rows = _load(_AAPL_DAILY)
    assert isinstance(rows, list) and rows
    keys = set(rows[0])
    assert "marketCap" in keys
    assert rows[0]["marketCap"] == pytest.approx(2887212881280.0)
    assert "floatMarketCap" not in keys
    assert "float_mcap" not in keys
    assert keys == {
        "date",
        "marketCap",
        "enterpriseVal",
        "peRatio",
        "pbRatio",
        "trailingPEG1Y",
    }


def test_map_eod_to_market_cap_raises_named_exception() -> None:
    rows = _load_eod(_AAPL_EOD)
    with pytest.raises(TiingoMarketCapUnavailableError, match="cannot produce a market-cap"):
        map_eod_to_market_cap(rows, _AAPL_ID)


def test_map_eod_to_market_cap_raises_on_empty_rows_rather_than_fabricating() -> None:
    with pytest.raises(TiingoMarketCapUnavailableError):
        map_eod_to_market_cap([], _AAPL_ID)


def test_map_eod_to_market_cap_does_not_return_a_frame() -> None:
    """Fail closed: never a schema-conformant but made-up number."""
    try:
        result = map_eod_to_market_cap(_load_eod(_AAPL_EOD), _AAPL_ID)
    except TiingoMarketCapUnavailableError:
        return
    raise AssertionError(
        f"map_eod_to_market_cap returned {type(result)!r} instead of raising"
    )


# ---------------------------------------------------------------------------
# 6. Schema conformance
# ---------------------------------------------------------------------------
def test_raw_returns_schema_conformance() -> None:
    frame = map_eod_to_raw_returns(_load_eod(_AAPL_EOD), _AAPL_ID)
    validate_panel(frame, PIT_RAW_RETURN_PANEL_SCHEMA, name="raw_returns")
    assert (frame[STOCK_COL] == _AAPL_ID).all()


def test_trading_status_schema_conformance() -> None:
    frame = map_eod_to_trading_status(_load_eod(_TWTR_EOD), _TWTR_ID)
    validate_panel(frame, PIT_TRADING_STATUS_SCHEMA, name="trading_status")
    assert (frame[STOCK_COL] == _TWTR_ID).all()


def test_empty_eod_rows_still_schema_conformant() -> None:
    returns = map_eod_to_raw_returns([], _AAPL_ID)
    status = map_eod_to_trading_status([], _AAPL_ID)
    validate_panel(returns, PIT_RAW_RETURN_PANEL_SCHEMA, name="raw_returns")
    validate_panel(status, PIT_TRADING_STATUS_SCHEMA, name="trading_status")
    assert returns.empty
    assert status.empty


# ---------------------------------------------------------------------------
# 7. Provenance columns, and extra columns do not break validate_panel
# ---------------------------------------------------------------------------
def test_provenance_columns_on_raw_returns() -> None:
    frame = map_eod_to_raw_returns(_load_eod(_AAPL_EOD), _AAPL_ID)
    _assert_provenance(frame, source_endpoint="get_eod_prices")
    validate_panel(frame, PIT_RAW_RETURN_PANEL_SCHEMA, name="raw_returns")


def test_provenance_columns_on_trading_status() -> None:
    frame = map_eod_to_trading_status(_load_eod(_TWTR_EOD), _TWTR_ID)
    _assert_provenance(frame, source_endpoint="get_eod_prices")
    validate_panel(frame, PIT_TRADING_STATUS_SCHEMA, name="trading_status")


def test_source_endpoint_override_is_recorded() -> None:
    frame = map_eod_to_raw_returns(
        _load_eod(_AAPL_EOD), _AAPL_ID, source_endpoint="custom_endpoint"
    )
    assert (frame["_source_endpoint"] == "custom_endpoint").all()


def test_china_a_share_flags_are_omitted_not_emitted_as_false() -> None:
    frame = map_eod_to_trading_status(_load_eod(_TWTR_EOD), _TWTR_ID)
    for col in _CHINA_A_FLAGS:
        assert col not in frame.columns


def _assert_provenance(frame: pd.DataFrame, *, source_endpoint: str) -> None:
    for col in _PROVENANCE_COLS:
        assert col in frame.columns, f"missing provenance column {col}"
    assert (frame["_source_vendor"] == "tiingo").all()
    assert (frame["_source_endpoint"] == source_endpoint).all()
    assert frame["_ingested_at"].notna().all()


# ---------------------------------------------------------------------------
# Defensive sort + client replay (not required, but cheap correctness)
# ---------------------------------------------------------------------------
def test_unsorted_eod_rows_are_sorted_defensively() -> None:
    rows = list(reversed(_load_eod(_AAPL_EOD)))
    assert rows[0]["date"].startswith("2020-09-04")

    returns = map_eod_to_raw_returns(rows, _AAPL_ID)
    status = map_eod_to_trading_status(rows, _AAPL_ID)

    assert list(returns[DATE_COL]) == sorted(returns[DATE_COL].tolist())
    assert list(status[DATE_COL]) == sorted(status[DATE_COL].tolist())
    assert pd.Timestamp("2020-08-20").date() not in _dates(returns)
    assert pd.Timestamp("2020-08-21").date() in _dates(returns)
    # Ordinary-day return still computed against the true prior close.
    expected = 497.48 / 473.1 - 1.0
    assert _row_on(returns, "2020-08-21")[RAW_RETURN_COL] == pytest.approx(expected)


def test_replay_transport_eod_maps_identically_to_disk_fixture() -> None:
    body = _load_eod(_AAPL_EOD)
    client = TiingoClient(
        transport=replay_transport({"/tiingo/daily/AAPL/prices": (200, body)})
    )
    fetched = client.get_eod_prices("AAPL", "2020-08-20", "2020-09-05")
    assert fetched == body
    from_disk = map_eod_to_raw_returns(body, _AAPL_ID)
    from_client = map_eod_to_raw_returns(fetched, _AAPL_ID)
    pd.testing.assert_series_equal(
        from_disk[RAW_RETURN_COL], from_client[RAW_RETURN_COL]
    )
    pd.testing.assert_series_equal(from_disk[DATE_COL], from_client[DATE_COL])
