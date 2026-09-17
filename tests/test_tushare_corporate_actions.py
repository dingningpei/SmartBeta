"""Tests for the Tushare corporate-action mapping (Phase 4D-B, P4DB-5).

Every test is offline. The fixtures under
``tests/fixtures/tushare/corporate_actions/`` are **proxy-observed live
evidence** recorded through ``pcd.mobcvb.cn/tushare/pro`` on 2026-09-17
(three identical samples per request, canonical-consistency checked at
recording time). They are the third-party proxy's real delivered data.
They are **not** a capture of the paid official Tushare API and nothing
here certifies direct official-Tushare behavior; see the manifest's
``_provenance`` and the module docstring.

An autouse tripwire replaces ``urllib.request.urlopen`` so an accidental
live call fails the offending test loudly. The fixture-replay transport is
still exercised through the real :class:`ProxyTushareClient` in one test to
prove the offline path is end-to-end, not just a direct dict call.
"""

from __future__ import annotations

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
)
from smart_beta.vendors.tushare.corporate_actions import (
    ACTION_TYPE_ADJ_FACTOR_STEP,
    ACTION_TYPE_DIVIDEND,
    ACTION_TYPE_SPLIT,
    ACTION_TYPE_SPLIT_AND_DIVIDEND,
    IMPLEMENTED_DIV_PROC,
    adjustment_factor_for_dividend,
    map_adj_factor_to_corporate_actions,
    map_dividend_to_corporate_actions,
)
from smart_beta.vendors.tushare.proxy_client import (
    ProxyTushareClient,
    fixture_key,
    replay_transport,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "tushare" / "corporate_actions"
MANIFEST_PATH = FIXTURE_DIR / "manifest.json"

DIVIDEND = "dividend_ts_code-000001.SZ.json"
ADJ_2013 = "adj_factor_end_date-20131231_start_date-20130101_ts_code-000001.SZ.json"
ADJ_2012 = "adj_factor_end_date-20121231_start_date-20120101_ts_code-000001.SZ.json"
DAILY_2013 = "daily_end_date-20130701_start_date-20130601_ts_code-000001.SZ.json"
DAILY_2012 = "daily_end_date-20121031_start_date-20121001_ts_code-000001.SZ.json"
DAILY_2014 = "daily_end_date-20140701_start_date-20140601_ts_code-000001.SZ.json"
DAILY_BASIC_2013 = (
    "daily_basic_end_date-20130701_start_date-20130601_ts_code-000001.SZ.json"
)
DAILY_BASIC_2014 = (
    "daily_basic_end_date-20140701_start_date-20140601_ts_code-000001.SZ.json"
)

#: Tolerances, stated once. The frozen formula and the independently
#: observed adj_factor ratio disagree only because the vendor rounds
#: adj_factor to three decimals; 0.1% is far above the observed ~0.01%.
RATIO_REL_TOLERANCE = 1e-3

#: Documented specimen constants (proxy-observed).
P2013 = {
    "ex_date": "20130620",
    "early_ann": "20130308",
    "late_ann": "20130524",
    "stk_div": 0.6,
    "cash_div_tax": 0.17,
    "prev_close": 19.24,  # daily.close 2013-06-19
    "ex_pre_close": 11.92,  # daily.pre_close 2013-06-20
    "adj_prev": 36.173,  # adj_factor 2013-06-19
    "adj_new": 58.387,  # adj_factor 2013-06-20
    "share_before": 512335.0,
    "share_after": 819736.0,
}
P2014 = {
    "ex_date": "20140612",
    "early_ann": "20140307",
    "late_ann": "20140523",
    "stk_div": 0.2,
    "cash_div_tax": 0.16,
    "prev_close": 11.78,  # daily.close 2014-06-11
    "share_before": 952075.0,
    "share_after": 1142490.0,
}
P2012_CASH = {
    "ex_date": "20121019",
    "early_ann": "20120816",
    "late_ann": "20120901",
    "stk_div": 0.0,
    "cash_div_tax": 0.1,
    "prev_close": 13.51,  # daily.close 2012-10-18
    "adj_prev": 35.906,
    "adj_new": 36.173,
}


# ---------------------------------------------------------------------------
# Fixture loading
# ---------------------------------------------------------------------------
def _manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text())


def _body(filename: str) -> object:
    return json.loads((FIXTURE_DIR / filename).read_text())


def _payload(filename: str) -> dict:
    return _body(filename)["data"]


def _rows(filename: str) -> list[dict]:
    payload = _payload(filename)
    fields = payload["fields"]
    return [dict(zip(fields, item)) for item in payload["items"]]


def _recorded_client() -> ProxyTushareClient:
    recordings = {}
    for name, entry in _manifest()["recordings"].items():
        recordings[fixture_key(entry["api_name"], entry["params"])] = (
            entry["status_code"],
            _body(name),
        )
    return ProxyTushareClient(transport=replay_transport(recordings))


def _row_for_date(frame: pd.DataFrame, date: str) -> pd.Series:
    matches = frame.loc[frame[EFFECTIVE_DATE_COL] == pd.Timestamp(date)]
    assert len(matches) == 1, f"expected exactly one row on {date}"
    return matches.iloc[0]


def _total_share_ratio(filename: str, before: str, after: str) -> float:
    rows = {r["trade_date"]: r["total_share"] for r in _rows(filename)}
    return float(rows[after]) / float(rows[before])


def _daily_row(filename: str, trade_date: str) -> dict:
    rows = {r["trade_date"]: r for r in _rows(filename)}
    return rows[trade_date]


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail loudly on any accidental live call in this module."""

    def _tripwire(*args: object, **kwargs: object) -> object:
        raise AssertionError("network access attempted in an offline test")

    monkeypatch.setattr(urllib.request, "urlopen", _tripwire)


# ---------------------------------------------------------------------------
# 1. Schema conformance and shape
# ---------------------------------------------------------------------------
def test_dividend_mapping_conforms_to_schema() -> None:
    frame = map_dividend_to_corporate_actions(_payload(DIVIDEND))
    CORPORATE_ACTIONS_SCHEMA.validate(frame, name="corporate_actions")
    assert pd.api.types.is_datetime64_any_dtype(frame[EFFECTIVE_DATE_COL])
    assert pd.api.types.is_datetime64_any_dtype(frame[KNOWLEDGE_DATE_COL])
    assert frame[IS_SUPERSEDED_COL].dtype == bool
    assert (frame[IS_SUPERSEDED_COL] == False).all()  # noqa: E712
    assert set(frame[ACTION_TYPE_COL]) == {
        ACTION_TYPE_SPLIT,
        ACTION_TYPE_DIVIDEND,
        ACTION_TYPE_SPLIT_AND_DIVIDEND,
    }


def test_empty_payload_returns_schema_conforming_empty_frame() -> None:
    frame = map_dividend_to_corporate_actions({"fields": [], "items": []})
    CORPORATE_ACTIONS_SCHEMA.validate(frame, name="corporate_actions")
    assert frame.empty


def test_end_to_end_offline_fetch_then_map() -> None:
    """Exercise the real ProxyTushareClient over the recorded fixtures."""
    client = _recorded_client()
    payload = client.fetch("dividend", ts_code="000001.SZ", retry_on_empty=True)
    frame = map_dividend_to_corporate_actions(
        payload,
        prev_close_by_ex_date={P2013["ex_date"]: P2013["prev_close"]},
    )
    row = _row_for_date(frame, "2013-06-20")
    assert row[ACTION_TYPE_COL] == ACTION_TYPE_SPLIT_AND_DIVIDEND


# ---------------------------------------------------------------------------
# 2. The real 2013-06-20 event
# ---------------------------------------------------------------------------
def test_2013_event_dates_and_knowledge_policy() -> None:
    frame = map_dividend_to_corporate_actions(
        _payload(DIVIDEND),
        prev_close_by_ex_date={P2013["ex_date"]: P2013["prev_close"]},
    )
    row = _row_for_date(frame, "2013-06-20")
    assert row[STOCK_COL] == "000001.SZ"
    assert row[ACTION_TYPE_COL] == ACTION_TYPE_SPLIT_AND_DIVIDEND
    # effective_date is the vendor ex_date, unchanged.
    assert row[EFFECTIVE_DATE_COL] == pd.Timestamp(P2013["ex_date"])
    # knowledge_date is the earliest implemented ann_date carrying these terms.
    assert row[KNOWLEDGE_DATE_COL] == pd.Timestamp(P2013["early_ann"])
    assert row[KNOWLEDGE_DATE_COL] < row[EFFECTIVE_DATE_COL]
    assert row["_cash_adjustment_included"] == True  # noqa: E712
    assert row["_cash_div_basis"] == P2013["cash_div_tax"]


def test_2013_dual_announcement_rows_differ_only_in_ann_date() -> None:
    """Evidence for the knowledge-date policy: the two implemented rows carry
    byte-identical terms; only ``ann_date`` differs."""
    implemented = [
        row
        for row in _rows(DIVIDEND)
        if row["div_proc"] == IMPLEMENTED_DIV_PROC
        and row["ex_date"] == P2013["ex_date"]
    ]
    assert len(implemented) == 2
    assert {r["ann_date"] for r in implemented} == {
        P2013["early_ann"],
        P2013["late_ann"],
    }
    comparable = {k: v for k, v in implemented[0].items() if k != "ann_date"}
    for other in implemented[1:]:
        assert {k: v for k, v in other.items() if k != "ann_date"} == comparable


def test_2013_adjustment_factor_matches_frozen_formula() -> None:
    frame = map_dividend_to_corporate_actions(
        _payload(DIVIDEND),
        prev_close_by_ex_date={P2013["ex_date"]: P2013["prev_close"]},
    )
    row = _row_for_date(frame, "2013-06-20")
    expected = (
        (1.0 + P2013["stk_div"])
        * P2013["prev_close"]
        / (P2013["prev_close"] - P2013["cash_div_tax"])
    )
    assert row[ADJUSTMENT_FACTOR_COL] == pytest.approx(expected, rel=1e-12)
    # And the helper agrees with the frame.
    assert adjustment_factor_for_dividend(
        stk_div=P2013["stk_div"],
        cash_div_tax=P2013["cash_div_tax"],
        prev_close=P2013["prev_close"],
    ) == pytest.approx(expected, rel=1e-12)


def test_2013_factor_cross_checks_real_adj_factor_and_raw_price() -> None:
    """Proxy-observed cross-check (not official-Tushare certification)."""
    frame = map_dividend_to_corporate_actions(
        _payload(DIVIDEND),
        prev_close_by_ex_date={P2013["ex_date"]: P2013["prev_close"]},
    )
    factor = float(_row_for_date(frame, "2013-06-20")[ADJUSTMENT_FACTOR_COL])

    adj_ratio = P2013["adj_new"] / P2013["adj_prev"]
    # Raw-price reconciliation from the recorded daily fixture itself
    # (not from hardcoded numbers): last raw close before ex_date, divided
    # by the vendor's ex-date ex-rights reference pre_close.
    prev_close = float(_daily_row(DAILY_2013, "20130619")["close"])
    ex_pre_close = float(_daily_row(DAILY_2013, "20130620")["pre_close"])
    assert prev_close == P2013["prev_close"]
    assert ex_pre_close == P2013["ex_pre_close"]
    raw_ratio = prev_close / ex_pre_close
    assert factor == pytest.approx(adj_ratio, rel=RATIO_REL_TOLERANCE)
    assert factor == pytest.approx(raw_ratio, rel=RATIO_REL_TOLERANCE)

    # The same adj_factor ratio is independently reproduced from the recorded
    # adj_factor fixture, not hardcoded numbers.
    steps = map_adj_factor_to_corporate_actions(_payload(ADJ_2013))
    step = _row_for_date(steps, "2013-06-20")
    assert step[ACTION_TYPE_COL] == ACTION_TYPE_ADJ_FACTOR_STEP
    assert float(step[ADJUSTMENT_FACTOR_COL]) == pytest.approx(
        adj_ratio, rel=1e-12
    )
    assert factor == pytest.approx(
        float(step[ADJUSTMENT_FACTOR_COL]), rel=RATIO_REL_TOLERANCE
    )


def test_2013_share_count_ratio_matches_stk_div() -> None:
    ratio = _total_share_ratio(
        DAILY_BASIC_2013,
        before="20130603",
        after="20130620",
    )
    assert ratio == pytest.approx(1.0 + P2013["stk_div"], rel=1e-12)
    assert P2013["share_after"] / P2013["share_before"] == pytest.approx(1.6)


# ---------------------------------------------------------------------------
# 3. An additional real bonus-share event (2014-06-12)
# ---------------------------------------------------------------------------
def test_2014_bonus_event_is_internally_consistent() -> None:
    # The prior close is read from the recorded daily fixture.
    prev_close = float(_daily_row(DAILY_2014, "20140611")["close"])
    assert prev_close == P2014["prev_close"]
    frame = map_dividend_to_corporate_actions(
        _payload(DIVIDEND),
        prev_close_by_ex_date={P2014["ex_date"]: prev_close},
    )
    row = _row_for_date(frame, "2014-06-12")
    assert row[ACTION_TYPE_COL] == ACTION_TYPE_SPLIT_AND_DIVIDEND
    assert row[KNOWLEDGE_DATE_COL] == pd.Timestamp(P2014["early_ann"])
    assert row[KNOWLEDGE_DATE_COL] < row[EFFECTIVE_DATE_COL]
    assert row["_cash_div_basis"] == P2014["cash_div_tax"]

    expected = (
        (1.0 + P2014["stk_div"])
        * P2014["prev_close"]
        / (P2014["prev_close"] - P2014["cash_div_tax"])
    )
    assert row[ADJUSTMENT_FACTOR_COL] == pytest.approx(expected, rel=1e-12)

    # Independent share-count check from the real daily_basic total_share step.
    ratio = _total_share_ratio(
        DAILY_BASIC_2014,
        before="20140609",
        after="20140612",
    )
    assert ratio == pytest.approx(1.0 + P2014["stk_div"], rel=1e-12)
    assert ratio == pytest.approx(1.2, rel=1e-12)


# ---------------------------------------------------------------------------
# 4. Pure cash-dividend row: no spurious stock-split factor
# ---------------------------------------------------------------------------
def test_pure_cash_dividend_maps_without_split_factor() -> None:
    frame = map_dividend_to_corporate_actions(
        _payload(DIVIDEND),
        prev_close_by_ex_date={P2012_CASH["ex_date"]: P2012_CASH["prev_close"]},
    )
    row = _row_for_date(frame, "2012-10-19")
    assert row[ACTION_TYPE_COL] == ACTION_TYPE_DIVIDEND
    assert row["_cash_div_basis"] == P2012_CASH["cash_div_tax"]

    # The frozen cash-only factor, and explicitly NOT a stock-split factor.
    expected = P2012_CASH["prev_close"] / (
        P2012_CASH["prev_close"] - P2012_CASH["cash_div_tax"]
    )
    assert row[ADJUSTMENT_FACTOR_COL] == pytest.approx(expected, rel=1e-12)
    assert row[ADJUSTMENT_FACTOR_COL] != pytest.approx(
        1.0 + P2012_CASH["stk_div"], rel=1e-9
    )

    # Cross-check against the real adj_factor step for this cash-only event.
    adj_ratio = P2012_CASH["adj_new"] / P2012_CASH["adj_prev"]
    assert row[ADJUSTMENT_FACTOR_COL] == pytest.approx(
        adj_ratio, rel=RATIO_REL_TOLERANCE
    )
    raw_ratio = float(_daily_row(DAILY_2012, "20121018")["close"]) / float(
        _daily_row(DAILY_2012, "20121019")["pre_close"]
    )
    assert row[ADJUSTMENT_FACTOR_COL] == pytest.approx(
        raw_ratio, rel=RATIO_REL_TOLERANCE
    )


def test_pure_cash_dividend_without_price_has_no_split_factor() -> None:
    frame = map_dividend_to_corporate_actions(_payload(DIVIDEND))
    row = _row_for_date(frame, "2012-10-19")
    assert row[ACTION_TYPE_COL] == ACTION_TYPE_DIVIDEND
    assert row[ADJUSTMENT_FACTOR_COL] == pytest.approx(1.0, rel=1e-12)
    assert row["_cash_adjustment_included"] == False  # noqa: E712


def test_2013_without_price_falls_back_to_share_count_factor() -> None:
    frame = map_dividend_to_corporate_actions(_payload(DIVIDEND))
    row = _row_for_date(frame, "2013-06-20")
    assert row[ADJUSTMENT_FACTOR_COL] == pytest.approx(
        1.0 + P2013["stk_div"], rel=1e-12
    )
    assert row["_cash_adjustment_included"] == False  # noqa: E712


def test_non_implemented_rows_are_not_emitted_as_facts() -> None:
    """Proposal/approval/预披露 rows (e.g. the 2023-08-24 and 2026-08-15 zero
    rows) never become actions."""
    frame = map_dividend_to_corporate_actions(_payload(DIVIDEND))
    non_implemented = [
        row for row in _rows(DIVIDEND) if row["div_proc"] != IMPLEMENTED_DIV_PROC
    ]
    assert {row["ann_date"] for row in non_implemented} >= {"20230824", "20260815"}
    # Those announcement dates are not ex-dates and must not appear anywhere
    # in the mapped panel (as effective_date or knowledge_date).
    mapped_dates = set(frame[EFFECTIVE_DATE_COL]) | set(frame[KNOWLEDGE_DATE_COL])
    assert pd.Timestamp("2023-08-24") not in mapped_dates
    assert pd.Timestamp("2026-08-15") not in mapped_dates
    # An implemented-but-empty synthetic row is also dropped, not emitted.
    synthetic = {
        "fields": [
            "ts_code",
            "end_date",
            "ann_date",
            "div_proc",
            "stk_div",
            "cash_div",
            "cash_div_tax",
            "ex_date",
        ],
        "items": [
            [
                "000001.SZ",
                "20221231",
                "20230309",
                IMPLEMENTED_DIV_PROC,
                0.0,
                0.0,
                0.0,
                "20230614",
            ]
        ],
    }
    assert map_dividend_to_corporate_actions(synthetic).empty


# ---------------------------------------------------------------------------
# 5. adj_factor step reconstruction
# ---------------------------------------------------------------------------
def test_adj_factor_step_mapping_conforms_and_reconstructs_steps() -> None:
    steps_2013 = map_adj_factor_to_corporate_actions(_payload(ADJ_2013))
    CORPORATE_ACTIONS_SCHEMA.validate(steps_2013, name="corporate_actions")
    # The 2013 window has exactly one step (36.173 -> 58.387 on the ex-date).
    assert len(steps_2013) == 1
    step = steps_2013.iloc[0]
    assert step[EFFECTIVE_DATE_COL] == pd.Timestamp("2013-06-20")
    assert step[KNOWLEDGE_DATE_COL] == step[EFFECTIVE_DATE_COL]
    assert step[ACTION_TYPE_COL] == ACTION_TYPE_ADJ_FACTOR_STEP
    assert float(step[ADJUSTMENT_FACTOR_COL]) == pytest.approx(
        P2013["adj_new"] / P2013["adj_prev"], rel=1e-12
    )
    assert float(step["_prev_adj_factor"]) == P2013["adj_prev"]
    assert float(step["_new_adj_factor"]) == P2013["adj_new"]

    steps_2012 = map_adj_factor_to_corporate_actions(_payload(ADJ_2012))
    assert len(steps_2012) == 1
    cash_step = steps_2012.iloc[0]
    assert cash_step[EFFECTIVE_DATE_COL] == pd.Timestamp("2012-10-19")
    assert float(cash_step[ADJUSTMENT_FACTOR_COL]) == pytest.approx(
        P2012_CASH["adj_new"] / P2012_CASH["adj_prev"], rel=1e-12
    )


def test_adj_factor_empty_payload_is_schema_conforming() -> None:
    frame = map_adj_factor_to_corporate_actions({"fields": [], "items": []})
    CORPORATE_ACTIONS_SCHEMA.validate(frame, name="corporate_actions")
    assert frame.empty


# ---------------------------------------------------------------------------
# 6. Fail-closed behavior and provenance
# ---------------------------------------------------------------------------
def test_impossible_prev_close_raises() -> None:
    with pytest.raises(ValueError):
        map_dividend_to_corporate_actions(
            _payload(DIVIDEND),
            prev_close_by_ex_date={P2013["ex_date"]: 0.1},
        )


def test_missing_implemented_ann_date_raises() -> None:
    synthetic = {
        "fields": ["ts_code", "ann_date", "div_proc", "stk_div", "cash_div", "ex_date"],
        "items": [["000001.SZ", None, IMPLEMENTED_DIV_PROC, 0.5, 0.0, "20200101"]],
    }
    with pytest.raises(ValueError):
        map_dividend_to_corporate_actions(synthetic)


def test_provenance_columns_and_source_vendor() -> None:
    frame = map_dividend_to_corporate_actions(_payload(DIVIDEND))
    assert (frame["_source_vendor"] == "tushare").all()
    assert (frame["_source_endpoint"] == "dividend").all()
    assert frame["_ingested_at"].notna().all()


def test_fixture_provenance_is_explicitly_proxy_observed() -> None:
    manifest = _manifest()
    provenance = manifest["_provenance"]
    assert provenance["live_recorded"] is True
    assert provenance["evidence_class"] == "proxy-observed live evidence"
    assert "proxy:pcd.mobcvb.cn" in provenance["access_path"]
    assert "official" in provenance["not_evidence_for"].lower()
    assert provenance["samples_per_request"] == 3


def test_row_mappings_are_not_mutated() -> None:
    rows = _rows(DIVIDEND)
    before = json.dumps(rows, sort_keys=True, default=str)
    map_dividend_to_corporate_actions(rows)
    assert json.dumps(rows, sort_keys=True, default=str) == before
