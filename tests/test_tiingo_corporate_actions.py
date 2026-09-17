"""Tests for :mod:`smart_beta.vendors.tiingo.corporate_actions`.

Every test here is offline. Fixtures are the recorded (or, for the
same-date combination, explicitly constructed) specimens under
``tests/fixtures/tiingo/corporate_actions/``. The mapper takes already-
fetched EOD rows; it performs no I/O. An autouse tripwire still replaces
``urllib.request.urlopen`` so an accidental live call fails loudly.

Required coverage (P4B-5):

1. AAPL 2020-08-31 split factor mapping.
2. Dividend factor formula, hand-verified, plus an independent local
   reimplementation of ``(1+raw_ret)*factor-1`` (not
   ``compute_adjusted_returns``).
3. Vendor semantics, independently confirmed against Nasdaq.
4. No pre-adjustment leakage in output columns (or production reads).
5. Same-date split+dividend combination (constructed fixture).
6. Schema conformance via ``validate_panel``.
7. No input mutation of ``eod_rows``.
8. Provenance columns present.
"""

from __future__ import annotations

import copy
import inspect
import json
import urllib.request
from pathlib import Path

import pandas as pd
import pytest

from smart_beta.pit.schema import (
    ACTION_TYPE_COL,
    ADJUSTMENT_FACTOR_COL,
    CORPORATE_ACTIONS_SCHEMA,
    EFFECTIVE_DATE_COL,
    IS_SUPERSEDED_COL,
    KNOWLEDGE_DATE_COL,
    STOCK_COL,
    validate_panel,
)
from smart_beta.vendors.tiingo.corporate_actions import (
    ACTION_TYPE_DIVIDEND,
    ACTION_TYPE_SPLIT,
    CLOSE_FIELD,
    DATE_FIELD,
    DIV_CASH_FIELD,
    DIVIDEND_SEMANTICS_CERTIFIED,
    SPLIT_FACTOR_FIELD,
    map_eod_to_corporate_actions,
)

_FIXTURE_DIR = (
    Path(__file__).resolve().parent / "fixtures" / "tiingo" / "corporate_actions"
)

_STOCK_ID = "AAPL"

# Hand-read from aapl_eod_dividend_2020-07-28_2020-08-14.json:
# 2020-08-06 close=455.61 (cum-dividend), 2020-08-07 close=444.45 divCash=0.82.
_EX_DIV_DATE = "2020-08-07"
_EX_DIV_CLOSE = 444.45
_PREV_CLOSE = 455.61
_DIV_CASH = 0.82

# Independently sourced (Nasdaq Dividend History API, 2026-09-17) and
# corroborated by Apple 8-K exhibit 99.1 filed 2020-07-30.
_INDEPENDENT_EX_DATE = "2020-08-07"
_INDEPENDENT_AMOUNT = 0.82
_DECLARATION_DATE = "2020-07-30"
_RECORD_DATE = "2020-08-10"
_PAYMENT_DATE = "2020-08-13"


def _load_eod(filename: str) -> list[dict]:
    return json.loads((_FIXTURE_DIR / filename).read_text(encoding="utf-8"))


def _load_json(filename: str) -> dict:
    return json.loads((_FIXTURE_DIR / filename).read_text(encoding="utf-8"))


def _naive(value: str) -> pd.Timestamp:
    return pd.Timestamp(value).normalize()


def _row_on(eod_rows: list[dict], day: str) -> dict:
    target = _naive(day)
    for row in eod_rows:
        if _naive(str(row[DATE_FIELD])[:10]) == target:
            return row
    raise AssertionError(f"no EOD row for {day}")


def _actions_of_type(frame: pd.DataFrame, action_type: str) -> pd.DataFrame:
    return frame.loc[frame[ACTION_TYPE_COL] == action_type]


@pytest.fixture(autouse=True)
def _forbid_live_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*args: object, **kwargs: object) -> object:
        raise AssertionError(
            "Test attempted a live network call; load fixtures from disk."
        )

    monkeypatch.setattr(urllib.request, "urlopen", _boom)


# ---------------------------------------------------------------------------
# 1. Split factor mapping (AAPL 2020-08-31 4-for-1)
# ---------------------------------------------------------------------------


def test_aapl_split_factor_is_tiingo_value_unmodified() -> None:
    eod_rows = _load_eod("aapl_eod_split_2020-08-20_2020-09-05.json")
    split_row = _row_on(eod_rows, "2020-08-31")
    assert split_row[SPLIT_FACTOR_FIELD] == 4.0
    assert split_row[DIV_CASH_FIELD] == 0.0

    out = map_eod_to_corporate_actions(eod_rows, _STOCK_ID)
    splits = _actions_of_type(out, ACTION_TYPE_SPLIT)

    assert len(splits) == 1
    mapped = splits.iloc[0]
    assert mapped[ACTION_TYPE_COL] == ACTION_TYPE_SPLIT
    assert mapped[ADJUSTMENT_FACTOR_COL] == 4.0
    assert mapped[STOCK_COL] == _STOCK_ID
    assert mapped[EFFECTIVE_DATE_COL] == _naive("2020-08-31")
    assert mapped[KNOWLEDGE_DATE_COL] == mapped[EFFECTIVE_DATE_COL]
    assert mapped[IS_SUPERSEDED_COL] is False or mapped[IS_SUPERSEDED_COL] == False


def test_non_split_days_emit_no_split_row() -> None:
    eod_rows = _load_eod("aapl_eod_split_2020-08-20_2020-09-05.json")
    out = map_eod_to_corporate_actions(eod_rows, _STOCK_ID)
    assert set(out[EFFECTIVE_DATE_COL]) == {_naive("2020-08-31")}
    assert set(out[ACTION_TYPE_COL]) == {ACTION_TYPE_SPLIT}


# ---------------------------------------------------------------------------
# 2. Dividend factor formula, hand-verified + anti-tautology
# ---------------------------------------------------------------------------


def test_dividend_factor_matches_hand_computed_one_plus_div_over_close() -> None:
    eod_rows = _load_eod("aapl_eod_dividend_2020-07-28_2020-08-14.json")
    ex_row = _row_on(eod_rows, _EX_DIV_DATE)
    assert ex_row[DIV_CASH_FIELD] == _DIV_CASH
    assert ex_row[CLOSE_FIELD] == _EX_DIV_CLOSE

    # Hand calculation from the recorded numbers:
    #   1 + 0.82 / 444.45 = 1.0018452014854317
    hand_factor = 1.0 + _DIV_CASH / _EX_DIV_CLOSE
    assert hand_factor == pytest.approx(1.0018452014854317)

    out = map_eod_to_corporate_actions(eod_rows, _STOCK_ID)
    dividends = _actions_of_type(out, ACTION_TYPE_DIVIDEND)
    assert len(dividends) == 1
    mapped = dividends.iloc[0]
    assert mapped[ACTION_TYPE_COL] == ACTION_TYPE_DIVIDEND
    assert mapped[EFFECTIVE_DATE_COL] == _naive(_EX_DIV_DATE)
    assert mapped[KNOWLEDGE_DATE_COL] == mapped[EFFECTIVE_DATE_COL]
    assert mapped[ADJUSTMENT_FACTOR_COL] == pytest.approx(hand_factor)
    assert mapped[ADJUSTMENT_FACTOR_COL] == pytest.approx(1.0 + _DIV_CASH / _EX_DIV_CLOSE)


def test_dividend_factor_recovers_total_return_from_raw_exdiv_return() -> None:
    """Anti-tautology: the frozen formula is the unique factor that makes
    ``(1 + raw_ret) * factor - 1`` equal the cash-inclusive total return.
    Reimplemented locally; does not import ``compute_adjusted_returns``.
    """
    eod_rows = _load_eod("aapl_eod_dividend_2020-07-28_2020-08-14.json")
    prev = _row_on(eod_rows, "2020-08-06")
    ex_row = _row_on(eod_rows, _EX_DIV_DATE)
    assert prev[CLOSE_FIELD] == _PREV_CLOSE
    assert ex_row[CLOSE_FIELD] == _EX_DIV_CLOSE
    assert ex_row[DIV_CASH_FIELD] == _DIV_CASH

    raw_ret = (_EX_DIV_CLOSE - _PREV_CLOSE) / _PREV_CLOSE
    true_total = (_EX_DIV_CLOSE + _DIV_CASH - _PREV_CLOSE) / _PREV_CLOSE
    # Hand values:
    #   raw_ret  = (444.45 - 455.61) / 455.61 = -0.024494633568183315
    #   true_total = (444.45 + 0.82 - 455.61) / 455.61 = -0.022694849103400276
    assert raw_ret == pytest.approx(-0.024494633568183315)
    assert true_total == pytest.approx(-0.022694849103400276)

    out = map_eod_to_corporate_actions(eod_rows, _STOCK_ID)
    factor = float(_actions_of_type(out, ACTION_TYPE_DIVIDEND).iloc[0][ADJUSTMENT_FACTOR_COL])

    recovered = (1.0 + raw_ret) * factor - 1.0
    assert recovered == pytest.approx(true_total, abs=1e-12)

    # Using the previous day's close in the factor would NOT recover it.
    wrong_factor = 1.0 + _DIV_CASH / _PREV_CLOSE
    wrong_recovered = (1.0 + raw_ret) * wrong_factor - 1.0
    assert wrong_recovered != pytest.approx(true_total, abs=1e-12)


# ---------------------------------------------------------------------------
# 3. Vendor semantics, independently confirmed (distinct from test 2)
# ---------------------------------------------------------------------------


def test_dividend_semantics_certified_flag_matches_investigation() -> None:
    assert DIVIDEND_SEMANTICS_CERTIFIED is True


def test_tiingo_divcash_matches_independent_ex_date_and_amount() -> None:
    independent = _load_json("independent_nasdaq_aapl_dividend_2020-08-07.json")
    nasdaq = independent["row"]
    assert nasdaq["exOrEffDate"] == "08/07/2020"
    assert nasdaq["amount"] == "$0.82"
    assert nasdaq["declarationDate"] == "07/30/2020"
    assert nasdaq["recordDate"] == "08/10/2020"
    assert nasdaq["paymentDate"] == "08/13/2020"

    eod_rows = _load_eod("aapl_eod_dividend_2020-07-28_2020-08-14.json")
    ex_row = _row_on(eod_rows, _INDEPENDENT_EX_DATE)
    assert ex_row[DIV_CASH_FIELD] == _INDEPENDENT_AMOUNT
    assert ex_row[DIV_CASH_FIELD] == pytest.approx(0.82)

    # Attached to the ex-date, not declaration / record / payment.
    assert _row_on(eod_rows, _DECLARATION_DATE)[DIV_CASH_FIELD] == 0.0
    assert _row_on(eod_rows, _RECORD_DATE)[DIV_CASH_FIELD] == 0.0
    assert _row_on(eod_rows, _PAYMENT_DATE)[DIV_CASH_FIELD] == 0.0

    # Mapper emits the dividend on that same independent ex-date.
    out = map_eod_to_corporate_actions(eod_rows, _STOCK_ID)
    dividends = _actions_of_type(out, ACTION_TYPE_DIVIDEND)
    assert len(dividends) == 1
    assert dividends.iloc[0][EFFECTIVE_DATE_COL] == _naive(_INDEPENDENT_EX_DATE)


# ---------------------------------------------------------------------------
# 4. No pre-adjustment leakage
# ---------------------------------------------------------------------------


def test_output_columns_are_schema_plus_provenance_only() -> None:
    eod_rows = _load_eod("aapl_eod_split_2020-08-20_2020-09-05.json")
    out = map_eod_to_corporate_actions(eod_rows, _STOCK_ID)

    schema_cols = set(CORPORATE_ACTIONS_SCHEMA.key_columns) | set(
        CORPORATE_ACTIONS_SCHEMA.dtypes
    )
    provenance = {"_source_vendor", "_source_endpoint", "_ingested_at"}
    assert set(out.columns) <= schema_cols | provenance
    assert schema_cols <= set(out.columns)

    forbidden = {
        "adjClose",
        "adjOpen",
        "adjHigh",
        "adjLow",
        "adjVolume",
        "adj_ret",
        "adjClose".lower(),
    }
    assert set(out.columns).isdisjoint(forbidden)
    for col in out.columns:
        lowered = col.lower()
        if col == ADJUSTMENT_FACTOR_COL:
            continue
        assert "adjclose" not in lowered
        assert "adj_close" not in lowered
        assert not lowered.startswith("adj") or col == ADJUSTMENT_FACTOR_COL


def test_mapper_source_does_not_read_vendor_adjusted_prices() -> None:
    source = inspect.getsource(map_eod_to_corporate_actions)
    for token in ("adjClose", "adjOpen", "adjHigh", "adjLow", "adjVolume"):
        assert token not in source


# ---------------------------------------------------------------------------
# 5. Same-date split + dividend combination
# ---------------------------------------------------------------------------


def test_same_date_split_and_dividend_emits_both_rows() -> None:
    payload = _load_json("synthetic_same_date_split_and_dividend.json")
    assert payload["_constructed"] is True
    eod_rows = payload["rows"]

    out = map_eod_to_corporate_actions(eod_rows, "SYN")
    event_date = _naive("2020-01-15")
    on_date = out.loc[out[EFFECTIVE_DATE_COL] == event_date]
    assert len(on_date) == 2
    assert set(on_date[ACTION_TYPE_COL]) == {ACTION_TYPE_SPLIT, ACTION_TYPE_DIVIDEND}

    split_factor = float(
        on_date.loc[on_date[ACTION_TYPE_COL] == ACTION_TYPE_SPLIT, ADJUSTMENT_FACTOR_COL].iloc[0]
    )
    div_factor = float(
        on_date.loc[
            on_date[ACTION_TYPE_COL] == ACTION_TYPE_DIVIDEND, ADJUSTMENT_FACTOR_COL
        ].iloc[0]
    )
    assert split_factor == 2.0
    # 1 + 1.0 / 50.0 = 1.02
    assert div_factor == pytest.approx(1.02)


def test_same_date_combined_factor_recovers_hand_derived_economic_return() -> None:
    """Start with 1 share at $100. On t: 2-for-1 split and $1 cash per
    post-split share. End: 2 shares at $50 plus $2 cash = $102. True
    return = 0.02. Combined factor = 2.0 * 1.02 = 2.04 applied to the
    raw close-to-close return of (50-100)/100 = -0.5 recovers 0.02.
    """
    payload = _load_json("synthetic_same_date_split_and_dividend.json")
    eod_rows = payload["rows"]
    prev_close = eod_rows[0][CLOSE_FIELD]
    event = eod_rows[1]
    close_t = event[CLOSE_FIELD]
    div_cash = event[DIV_CASH_FIELD]
    split_factor = event[SPLIT_FACTOR_FIELD]

    out = map_eod_to_corporate_actions(eod_rows, "SYN")
    on_date = out.loc[out[EFFECTIVE_DATE_COL] == _naive("2020-01-15")]
    mapped_split = float(
        on_date.loc[on_date[ACTION_TYPE_COL] == ACTION_TYPE_SPLIT, ADJUSTMENT_FACTOR_COL].iloc[0]
    )
    mapped_div = float(
        on_date.loc[
            on_date[ACTION_TYPE_COL] == ACTION_TYPE_DIVIDEND, ADJUSTMENT_FACTOR_COL
        ].iloc[0]
    )
    combined = mapped_split * mapped_div
    assert combined == pytest.approx(2.04)

    raw_ret = (close_t - prev_close) / prev_close
    recovered = (1.0 + raw_ret) * combined - 1.0

    # Economic outcome, derived separately: post-split share count equals
    # splitFactor; cash is divCash per post-split share (the share whose
    # price is this row's close).
    ending_wealth = split_factor * close_t + split_factor * div_cash
    economic_return = (ending_wealth - prev_close) / prev_close
    assert economic_return == pytest.approx(0.02)
    assert recovered == pytest.approx(economic_return, abs=1e-12)
    assert recovered == pytest.approx(0.02)


# ---------------------------------------------------------------------------
# 6. Schema conformance
# ---------------------------------------------------------------------------


def test_split_fixture_validates_against_corporate_actions_schema() -> None:
    eod_rows = _load_eod("aapl_eod_split_2020-08-20_2020-09-05.json")
    out = map_eod_to_corporate_actions(eod_rows, _STOCK_ID)
    validate_panel(out, CORPORATE_ACTIONS_SCHEMA, name="corporate_actions")


def test_dividend_fixture_validates_against_corporate_actions_schema() -> None:
    eod_rows = _load_eod("aapl_eod_dividend_2020-07-28_2020-08-14.json")
    out = map_eod_to_corporate_actions(eod_rows, _STOCK_ID)
    validate_panel(out, CORPORATE_ACTIONS_SCHEMA, name="corporate_actions")


def test_empty_input_returns_schema_conforming_empty_panel() -> None:
    out = map_eod_to_corporate_actions([], _STOCK_ID)
    assert len(out) == 0
    validate_panel(out, CORPORATE_ACTIONS_SCHEMA, name="corporate_actions")


# ---------------------------------------------------------------------------
# 7. No input mutation
# ---------------------------------------------------------------------------


def test_eod_rows_are_not_mutated() -> None:
    eod_rows = _load_eod("aapl_eod_dividend_2020-07-28_2020-08-14.json")
    snapshot = copy.deepcopy(eod_rows)
    map_eod_to_corporate_actions(eod_rows, _STOCK_ID)
    assert eod_rows == snapshot


def test_constructed_rows_are_not_mutated() -> None:
    payload = _load_json("synthetic_same_date_split_and_dividend.json")
    eod_rows = payload["rows"]
    snapshot = copy.deepcopy(eod_rows)
    map_eod_to_corporate_actions(eod_rows, "SYN")
    assert eod_rows == snapshot


# ---------------------------------------------------------------------------
# 8. Provenance columns present
# ---------------------------------------------------------------------------


def test_provenance_columns_present() -> None:
    eod_rows = _load_eod("aapl_eod_split_2020-08-20_2020-09-05.json")
    out = map_eod_to_corporate_actions(
        eod_rows, _STOCK_ID, source_endpoint="get_eod_prices"
    )
    assert "_source_vendor" in out.columns
    assert "_source_endpoint" in out.columns
    assert "_ingested_at" in out.columns
    assert (out["_source_vendor"] == "tiingo").all()
    assert (out["_source_endpoint"] == "get_eod_prices").all()
    assert pd.api.types.is_datetime64_any_dtype(out["_ingested_at"])


def test_knowledge_date_equals_effective_date_when_no_announcement_field() -> None:
    eod_rows = _load_eod("aapl_eod_dividend_2020-07-28_2020-08-14.json")
    live_keys = set(eod_rows[0])
    assert "announcementDate" not in live_keys
    assert "declarationDate" not in live_keys
    assert "recordDate" not in live_keys
    assert "paymentDate" not in live_keys
    out = map_eod_to_corporate_actions(eod_rows, _STOCK_ID)
    assert (out[KNOWLEDGE_DATE_COL] == out[EFFECTIVE_DATE_COL]).all()
    assert (out[IS_SUPERSEDED_COL] == False).all()  # noqa: E712
