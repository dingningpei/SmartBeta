"""Tests for the Tushare fundamentals adapter (Phase 4D-B, P4DB-6).

All fixtures under ``tests/fixtures/tushare/fundamentals/`` are real
proxy-observed specimens recorded via ``ProxyTushareClient`` on
2026-09-17 (each request issued three times and canonical-consistency
checked). They are *proxy-observed* evidence: they do not certify direct
official-Tushare behavior. Every test replays them offline -- there is no
network access in ``pytest``.

The required policy tests are named individually so P4DB-9's certification
report can cite each one directly (see the task spec's required test list).
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from smart_beta.pit.schema import (
    FIELD_COL,
    FUNDAMENTALS_FACT_SCHEMA,
    IS_RESTATEMENT_COL,
    KNOWLEDGE_DATE_COL,
    REPORT_PERIOD_END_COL,
    STOCK_COL,
    VALUE_COL,
)
from smart_beta.vendors.tushare import fundamentals as F
from smart_beta.vendors.tushare.proxy_client import (
    ProxyTushareClient,
    fixture_key,
    replay_transport,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "tushare" / "fundamentals"
RETRIEVED_AT = pd.Timestamp("2026-09-17T00:00:00")


def _load_fixtures() -> tuple[dict, dict]:
    manifest = json.loads((FIXTURE_DIR / "manifest.json").read_text())
    payloads: dict = {}
    recordings: dict = {}
    for filename, meta in manifest["recordings"].items():
        body = json.loads((FIXTURE_DIR / filename).read_text())
        key = fixture_key(meta["api_name"], meta["params"])
        payloads[key] = body
        recordings[key] = (
            meta.get("status_code", 200),
            {"code": 0, "data": body},
        )
    return payloads, recordings


PAYLOADS, RECORDINGS = _load_fixtures()


def payload(api_name: str, **params) -> dict:
    """The real recorded payload for one request."""
    return PAYLOADS[fixture_key(api_name, params)]


def rows(api_name: str, **params) -> list[dict]:
    recorded = payload(api_name, **params)
    return [dict(zip(recorded["fields"], item)) for item in recorded["items"]]


def find_row(api_name: str, predicate, **params) -> dict:
    for row in rows(api_name, **params):
        if predicate(row):
            return row
    raise AssertionError(f"no row matched in {api_name} {params}")


def make_payload(records: list[dict], fields: list[str] | None = None) -> dict:
    if fields is None:
        fields = list(records[0].keys())
    return {
        "fields": fields,
        "items": [[record.get(field) for field in fields] for record in records],
    }


@pytest.fixture
def client() -> ProxyTushareClient:
    return ProxyTushareClient(transport=replay_transport(RECORDINGS))


def adapter(client: ProxyTushareClient, ts_codes: list[str]) -> F.TushareFundamentals:
    return F.TushareFundamentals(client, ts_codes, retrieved_at=RETRIEVED_AT)


# ===========================================================================
# Policy 3 -- knowledge-date rule, five lettered cases
# ===========================================================================
def test_knowledge_date_case_a_valid_f_ann_date() -> None:
    """Case A: a valid ``f_ann_date`` resolves to itself (real specimen)."""
    row = find_row(
        "income",
        lambda r: F.normalize_report_type(r["report_type"]) == "1",
        ts_code="000001.SZ",
        period="20220630",
    )
    decision = F.knowledge_date_decision(row)
    assert decision.drop_reason is None
    assert decision.knowledge_date == pd.Timestamp("2022-08-18")
    # Both dates agree in this real specimen; case A does not imply case B.
    assert row["ann_date"] == row["f_ann_date"] == "20220818"


def test_knowledge_date_case_a_divergent_f_ann_date_wins() -> None:
    """Case A, divergent: the real 002450.SZ FY2015 report_type=1 row.

    ``ann_date=20160422`` but ``f_ann_date=20210228`` -- the stale ann_date
    must NOT win.
    """
    row = find_row(
        "income",
        lambda r: F.normalize_report_type(r["report_type"]) == "1",
        ts_code="002450.SZ",
        period="20151231",
    )
    assert row["ann_date"] == "20160422"
    assert row["f_ann_date"] == "20210228"
    decision = F.knowledge_date_decision(row)
    assert decision.drop_reason is None
    assert decision.knowledge_date == pd.Timestamp("2021-02-28")
    assert decision.knowledge_date != pd.Timestamp("2016-04-22")


def test_knowledge_date_case_b_fallback_actually_fires() -> None:
    """Case B: ``f_ann_date`` genuinely missing, eligible report_type.

    CONSTRUCTED SPECIMEN. No real Tushare ``income``/``balancesheet``/
    ``cashflow`` row with a missing ``f_ann_date`` was found across the
    recorded specimens or a bounded additional live search (Tushare always
    populated ``f_ann_date`` there). This row is hand-constructed purely to
    exercise the fallback branch; it is not vendor ground truth.
    """
    row = {
        "ts_code": "000001.SZ",
        "ann_date": "20220427",
        "f_ann_date": None,
        "end_date": "20220331",
        "report_type": "1",
        "total_revenue": 1.0,
    }
    decision = F.knowledge_date_decision(row)
    assert decision.drop_reason is None
    assert decision.knowledge_date == pd.Timestamp("2022-04-27")


def test_knowledge_date_case_c_ineligible_report_type_dropped() -> None:
    """Case C: missing ``f_ann_date`` on an ineligible report_type -> drop.

    CONSTRUCTED SPECIMEN (same reason as case B: no real missing-f_ann_date
    row exists in the evidence). Also run through the adapter to assert the
    uncertainty side-table entry.
    """
    rows_in = [
        {
            "ts_code": "000001.SZ",
            "ann_date": "20190430",
            "f_ann_date": None,
            "end_date": "2022-09-30",
            "report_type": "4",
            "total_revenue": 1.0,
        }
    ]
    payload_in = make_payload(rows_in)
    facts, uncertain = F.map_statement_payload(
        payload_in, F.INCOME_ENDPOINT, ["total_revenue"], retrieved_at=RETRIEVED_AT
    )
    assert facts.empty
    assert len(uncertain) == 1
    assert uncertain.iloc[0][F.REASON_COL] == F.REASON_INELIGIBLE_REPORT_TYPE


def test_knowledge_date_case_d_malformed_f_ann_date_dropped() -> None:
    """Case D: a present-but-unparseable ``f_ann_date`` is never coerced."""
    rows_in = [
        {
            "ts_code": "000001.SZ",
            "ann_date": "20220427",
            "f_ann_date": "not-a-date",
            "end_date": "2022-09-30",
            "report_type": "1",
            "total_revenue": 1.0,
        }
    ]
    payload_in = make_payload(rows_in)
    facts, uncertain = F.map_statement_payload(
        payload_in, F.INCOME_ENDPOINT, ["total_revenue"], retrieved_at=RETRIEVED_AT
    )
    assert facts.empty
    assert len(uncertain) == 1
    assert uncertain.iloc[0][F.REASON_COL] == F.REASON_F_ANN_DATE_MALFORMED


def test_knowledge_date_case_e_both_dates_missing_dropped() -> None:
    """Case E: neither date usable -> drop with the combined reason."""
    rows_in = [
        {
            "ts_code": "000001.SZ",
            "ann_date": None,
            "f_ann_date": "",
            "end_date": "2022-09-30",
            "report_type": "1",
            "total_revenue": 1.0,
        }
    ]
    payload_in = make_payload(rows_in)
    facts, uncertain = F.map_statement_payload(
        payload_in, F.INCOME_ENDPOINT, ["total_revenue"], retrieved_at=RETRIEVED_AT
    )
    assert facts.empty
    assert len(uncertain) == 1
    assert uncertain.iloc[0][F.REASON_COL] == F.REASON_BOTH_DATES_MISSING


# ===========================================================================
# Policy 6 -- blank-out handling
# ===========================================================================
def test_blank_out_keeps_row_tags_known_missing_fy2016(
    client: ProxyTushareClient,
) -> None:
    """Real 002450.SZ FY2016 blank-out: row kept, value NaN, tagged."""
    frame = adapter(client, ["002450.SZ"]).get_fundamentals(
        "2016-12-31", "2016-12-31", ["total_revenue", "n_income"]
    )
    assert len(frame) == 2
    assert set(frame[FIELD_COL]) == {"total_revenue", "n_income"}
    assert frame[F.IS_BLANK_OUT_COL].all()
    assert frame[VALUE_COL].isna().all()
    # Distinct from "never reported": the row exists and is schema-visible.
    assert (frame[KNOWLEDGE_DATE_COL] == pd.Timestamp("2021-02-28")).all()


def test_blank_out_keeps_row_tags_known_missing_fy2017(
    client: ProxyTushareClient,
) -> None:
    """Real 002450.SZ FY2017 blank-out: row kept, value NaN, tagged."""
    frame = adapter(client, ["002450.SZ"]).get_fundamentals(
        "2017-12-31", "2017-12-31", ["total_revenue", "n_income"]
    )
    assert len(frame) == 2
    assert frame[F.IS_BLANK_OUT_COL].all()
    assert frame[VALUE_COL].isna().all()
    assert (frame[KNOWLEDGE_DATE_COL] == pd.Timestamp("2021-02-28")).all()


def test_blank_out_is_not_silently_coerced_to_zero(
    client: ProxyTushareClient,
) -> None:
    """An empty string must never become ``0.0``."""
    frame = adapter(client, ["002450.SZ"]).get_fundamentals(
        "2017-12-31", "2017-12-31", ["n_income"]
    )
    assert not (frame[VALUE_COL] == 0.0).any()


# ===========================================================================
# update_flag de-duplication and schema-key uniqueness
# ===========================================================================
def test_update_flag_duplicates_dedup_to_unique_schema_key(
    client: ProxyTushareClient,
) -> None:
    """Real 600518.SH FY2017 balancesheet ``money_cap`` appears twice per
    vintage (update_flag 0 and 1, byte-identical). After mapping there is
    exactly one row per knowledge_date, and both raw flags are recorded."""
    frame = adapter(client, ["600518.SH"]).get_fundamentals(
        "2017-12-31", "2017-12-31", ["money_cap"]
    )
    assert len(frame) == 2
    duplicate = frame.duplicated(
        subset=[STOCK_COL, REPORT_PERIOD_END_COL, FIELD_COL, KNOWLEDGE_DATE_COL]
    )
    assert not duplicate.any()
    assert set(frame[F.VINTAGE_ID_COL]) == {
        "1:20180426:20180426:0|1",
        "4:20190430:20190430:0|1",
    }
    FUNDAMENTALS_FACT_SCHEMA.validate(frame, name="update_flag_dedup")


def test_fundamentals_schema_conformance_full_extra_columns(
    client: ProxyTushareClient,
) -> None:
    """The fact frame conforms to the schema and carries every extra column."""
    frame = adapter(client, ["000001.SZ"]).get_fundamentals(
        "2022-06-30", "2022-06-30", ["total_revenue", "n_income"]
    )
    FUNDAMENTALS_FACT_SCHEMA.validate(frame, name="tushare_fundamentals")
    for column in (
        F.REPORTING_BASIS_COL,
        F.REPORT_TYPE_COL,
        F.VINTAGE_ID_COL,
        F.IS_BLANK_OUT_COL,
        F.SOURCE_VENDOR_COL,
        F.ACCESS_PATH_COL,
        F.RETRIEVED_AT_COL,
    ):
        assert column in frame.columns
    assert (frame[F.SOURCE_VENDOR_COL] == "Tushare").all()
    assert (frame[F.ACCESS_PATH_COL] == "proxy:pcd.mobcvb.cn").all()
    assert pd.api.types.is_datetime64_any_dtype(frame[F.RETRIEVED_AT_COL])


def test_no_duplicate_key_row_ever_reaches_output(
    client: ProxyTushareClient,
) -> None:
    frame = adapter(client, ["000001.SZ"]).get_fundamentals(
        "2022-12-31", "2022-12-31", ["total_revenue", "n_income"]
    )
    keys = [STOCK_COL, REPORT_PERIOD_END_COL, FIELD_COL, KNOWLEDGE_DATE_COL]
    assert not frame.duplicated(subset=keys).any()


def test_conflicting_report_type_rows_share_schema_key_is_surfaced(
    client: ProxyTushareClient,
) -> None:
    """Real 002450.SZ FY2015 income report_type=1 and 4 share the same
    ``knowledge_date`` but carry *different* values. No rule can honestly
    choose one, so the adapter refuses rather than guessing.

    This is the significant finding reported in the task write-up: the
    frozen ``update_flag`` de-duplication does not cover this real case.
    """
    with pytest.raises(F.TushareConflictingVintageError):
        adapter(client, ["002450.SZ"]).get_fundamentals(
            "2015-12-31", "2015-12-31", ["total_revenue"]
        )


# ===========================================================================
# Policy 8 -- reporting_basis
# ===========================================================================
def test_reporting_basis_income_cumulative_yTD(
    client: ProxyTushareClient,
) -> None:
    frame = adapter(client, ["000001.SZ"]).get_fundamentals(
        "2022-06-30", "2022-06-30", ["total_revenue"]
    )
    assert set(frame[F.REPORTING_BASIS_COL]) == {F.CUMULATIVE_YTD}


def test_reporting_basis_income_single_quarter(
    client: ProxyTushareClient,
) -> None:
    recorded = payload(
        "income", ts_code="000001.SZ", period="20220930", report_type="2"
    )
    facts, _ = F.map_statement_payload(
        recorded, F.INCOME_ENDPOINT, ["total_revenue"], retrieved_at=RETRIEVED_AT
    )
    assert len(facts) == 1
    assert facts.iloc[0][F.REPORTING_BASIS_COL] == F.SINGLE_QUARTER
    assert facts.iloc[0][F.REPORT_TYPE_COL] == "2"


def test_reporting_basis_balancesheet_instant(
    client: ProxyTushareClient,
) -> None:
    recorded = payload("balancesheet", ts_code="000001.SZ", period="20220930")
    facts, _ = F.map_statement_payload(
        recorded, F.BALANCESHEET_ENDPOINT, ["total_assets"], retrieved_at=RETRIEVED_AT
    )
    assert len(facts) == 1
    assert facts.iloc[0][F.REPORTING_BASIS_COL] == F.INSTANT


def test_reporting_basis_cashflow_cumulative_yTD(
    client: ProxyTushareClient,
) -> None:
    recorded = payload("cashflow", ts_code="000001.SZ", period="20220930")
    facts, _ = F.map_statement_payload(
        recorded, F.CASHFLOW_ENDPOINT, ["n_cashflow_act"], retrieved_at=RETRIEVED_AT
    )
    assert len(facts) >= 1
    assert set(facts[F.REPORTING_BASIS_COL]) == {F.CUMULATIVE_YTD}


def test_reporting_basis_fina_indicator_cumulative_yTD(
    client: ProxyTushareClient,
) -> None:
    """The CH3 row sourced from ``fina_indicator.profit_dedt`` is cumulative
    YTD, by analogy to income's own cumulative net-income fields -- and the
    real Q3 > H1 profit_dedt ordering confirms it."""
    frame = adapter(client, ["000001.SZ"]).get_fundamentals(
        "2022-09-30", "2022-09-30", [F.CH3_FIELD]
    )
    assert len(frame) == 1
    assert frame.iloc[0][F.REPORTING_BASIS_COL] == F.CUMULATIVE_YTD


def test_fina_indicator_profit_dedt_is_cumulative_not_single_quarter() -> None:
    """Empirical basis check: real 000001.SZ Q3 profit_dedt > H1 profit_dedt,
    so it is a YTD cumulative quantity, not a single-quarter figure."""
    q3 = rows("fina_indicator", ts_code="000001.SZ", period="20220930")[0]
    h1 = rows("fina_indicator", ts_code="000001.SZ", period="20220630")[0]
    assert float(q3["profit_dedt"]) > float(h1["profit_dedt"])
    # And it is close to income's own cumulative net income (net of
    # non-recurring items), never equal to the single-quarter value.
    single_quarter = rows(
        "income", ts_code="000001.SZ", period="20220930", report_type="2"
    )[0]
    assert float(q3["profit_dedt"]) > float(single_quarter["n_income"])


# ===========================================================================
# Policy 2 -- is_restatement
# ===========================================================================
def test_is_restatement_true_for_later_report_type4_vintage(
    client: ProxyTushareClient,
) -> None:
    """Real 600518.SH FY2017 pair: the 2019-04-30 report_type=4 vintage is a
    restatement of the 2018-04-26 report_type=1 original."""
    frame = adapter(client, ["600518.SH"]).get_fundamentals(
        "2017-12-31", "2017-12-31", ["total_revenue"]
    )
    by_date = frame.set_index(KNOWLEDGE_DATE_COL)
    assert bool(by_date.loc[pd.Timestamp("2019-04-30"), IS_RESTATEMENT_COL]) is True
    assert bool(by_date.loc[pd.Timestamp("2018-04-26"), IS_RESTATEMENT_COL]) is False
    assert (
        by_date.loc[pd.Timestamp("2019-04-30"), VALUE_COL]
        == pytest.approx(17_578_618_640.06)
    )
    assert (
        by_date.loc[pd.Timestamp("2018-04-26"), VALUE_COL]
        == pytest.approx(26_476_970_977.57)
    )


def test_is_restatement_true_for_in_place_ann_ne_f_ann(
    client: ProxyTushareClient,
) -> None:
    """Real 002069.SZ FY2017: one row, ``ann_date != f_ann_date`` -- the
    in-place reprocessing signal, so ``is_restatement=True`` even with no
    second vintage row (the vintage-coverage counterexample)."""
    frame = adapter(client, ["002069.SZ"]).get_fundamentals(
        "2017-12-31", "2017-12-31", ["total_revenue"]
    )
    assert len(frame) == 1
    row = frame.iloc[0]
    assert row[KNOWLEDGE_DATE_COL] == pd.Timestamp("2020-10-15")
    assert bool(row[IS_RESTATEMENT_COL]) is True


def test_is_restatement_signal_b_002450_fy2015_stale_ann() -> None:
    """Real 002450.SZ FY2015 report_type=1: stale ann_date (2016-04-22)
    but f_ann_date=2021-02-28 -- the in-place reprocessing signal.

    The full fact frame for this specimen is separately refused
    (``TushareConflictingVintageError``) because report_type=1 and 4 share
    its ``knowledge_date``; this test pins the underlying signal-b evidence
    on the raw real row. The adapter-level ``is_restatement=True`` path is
    exercised by ``test_is_restatement_true_for_in_place_ann_ne_f_ann``.
    """
    row = find_row(
        "income",
        lambda r: F.normalize_report_type(r["report_type"]) == "1",
        ts_code="002450.SZ",
        period="20151231",
    )
    assert row["ann_date"] != row["f_ann_date"]
    assert F.knowledge_date_decision(row).knowledge_date == pd.Timestamp(
        "2021-02-28"
    )


def test_is_restatement_false_when_no_evidence(
    client: ProxyTushareClient,
) -> None:
    """A clean single-vintage fact is False -- meaning "no evidence found",
    never "verified original"."""
    frame = adapter(client, ["000001.SZ"]).get_fundamentals(
        "2022-06-30", "2022-06-30", ["total_revenue"]
    )
    assert len(frame) == 1
    assert bool(frame.iloc[0][IS_RESTATEMENT_COL]) is False


# ===========================================================================
# Policy 7 -- CH3 join commands 1-3 and failure modes
# ===========================================================================
def test_ch3_join_condition3_fina_indicator_exposes_ann_date() -> None:
    """Blocking sub-investigation result: ``fina_indicator`` DOES expose its
    own ``ann_date`` field in every recorded real specimen."""
    recorded = payload("fina_indicator", ts_code="000001.SZ", period="20220930")
    assert "ann_date" in recorded["fields"]
    row = rows("fina_indicator", ts_code="000001.SZ", period="20220930")[0]
    assert row["ann_date"] == "20221025"


def test_ch3_fina_indicator_ann_date_never_diverged_in_recorded_specimens() -> None:
    """Characterization: for every recorded (stock, period) with both an
    income report_type=1 row and a fina_indicator row, the fina_indicator
    ``ann_date`` equals the anchor income ``ann_date``. Condition 3 was
    therefore always evaluable; no real divergence specimen was found."""
    pairs = [
        ("000001.SZ", "20220930"),
        ("000001.SZ", "20220630"),
        ("600518.SH", "20171231"),
        ("002450.SZ", "20151231"),
    ]
    checked = 0
    for ts_code, period in pairs:
        income_rows = rows("income", ts_code=ts_code, period=period)
        fina_rows = rows("fina_indicator", ts_code=ts_code, period=period)
        anchors = [
            r for r in income_rows if F.normalize_report_type(r["report_type"]) == "1"
        ]
        if not anchors or not fina_rows:
            continue
        assert anchors[0]["ann_date"] == fina_rows[0]["ann_date"]
        checked += 1
    assert checked == len(pairs)


def test_ch3_join_all_three_conditions_hold_clean_specimen(
    client: ProxyTushareClient,
) -> None:
    """Clean real specimen: 000001.SZ 2022-09-30. Conditions 1-3 all hold,
    so ``ni_ex_nonrecurring`` is emitted with the anchor's knowledge_date."""
    frame = adapter(client, ["000001.SZ"]).get_fundamentals(
        "2022-09-30", "2022-09-30", [F.CH3_FIELD]
    )
    assert len(frame) == 1
    row = frame.iloc[0]
    assert row[FIELD_COL] == F.CH3_FIELD
    assert row[VALUE_COL] == pytest.approx(36_597_000_000.0)
    assert row[KNOWLEDGE_DATE_COL] == pd.Timestamp("2022-10-25")
    assert bool(row[IS_RESTATEMENT_COL]) is False
    assert row[F.REPORTING_BASIS_COL] == F.CUMULATIVE_YTD


def test_ch3_suppressed_600518_fy2017_condition2(
    client: ProxyTushareClient,
) -> None:
    """Real 600518.SH FY2017: a report_type=4 restatement exists, so
    condition 2 fails -- no CH3 row, and an uncertainty entry."""
    frame = adapter(client, ["600518.SH"]).get_fundamentals(
        "2017-12-31", "2017-12-31", [F.CH3_FIELD]
    )
    assert frame.empty
    uncertain = adapter(client, ["600518.SH"]).get_uncertain_observations(
        "2017-12-31", "2017-12-31", [F.CH3_FIELD]
    )
    entry = uncertain[uncertain[FIELD_COL] == F.CH3_FIELD]
    assert len(entry) == 1
    assert entry.iloc[0][F.REASON_COL] == F.REASON_CH3_VINTAGE_JOIN_NOT_CERTIFIED
    # The fina ann_date was present and equal -- condition 3 was satisfied;
    # suppression is specifically condition 2.
    assert entry.iloc[0][F.RAW_FINA_ANN_DATE_COL] == "20180426"


def test_ch3_suppressed_002450_fy2015_condition2(
    client: ProxyTushareClient,
) -> None:
    """Real 002450.SZ FY2015: report_type 4 and 5 exist, so condition 2
    fails -- no CH3 row, and an uncertainty entry."""
    frame = adapter(client, ["002450.SZ"]).get_fundamentals(
        "2015-12-31", "2015-12-31", [F.CH3_FIELD]
    )
    assert frame.empty
    uncertain = adapter(client, ["002450.SZ"]).get_uncertain_observations(
        "2015-12-31", "2015-12-31", [F.CH3_FIELD]
    )
    entry = uncertain[uncertain[FIELD_COL] == F.CH3_FIELD]
    assert len(entry) == 1
    assert entry.iloc[0][F.REASON_COL] == F.REASON_CH3_VINTAGE_JOIN_NOT_CERTIFIED


def test_ch3_suppressed_when_condition3_fina_ann_diverges() -> None:
    """Conditions 1-2 hold but ``fina_indicator``'s own ``ann_date`` diverges
    from the anchor income ``ann_date``.

    CONSTRUCTED ``fina_indicator`` specimen (real income/balancesheet rows
    from 000001.SZ 2022-09-30; the fina ann_date is changed to 2022-10-26).
    No real divergence specimen was found in the recorded evidence, so this
    constructed case exercises the branch explicitly.
    """
    income = payload("income", ts_code="000001.SZ", period="20220930")
    balancesheet = payload("balancesheet", ts_code="000001.SZ", period="20220930")
    fina = make_payload(
        [
            {
                "ts_code": "000001.SZ",
                "ann_date": "20221026",
                "end_date": "20220930",
                "profit_dedt": 36_597_000_000.0,
            }
        ]
    )
    facts, uncertain = F.assemble_ch3_facts(
        income,
        balancesheet,
        fina,
        period_end=pd.Timestamp("2022-09-30"),
        retrieved_at=RETRIEVED_AT,
    )
    assert facts.empty
    assert len(uncertain) == 1
    assert uncertain.iloc[0][F.REASON_COL] == F.REASON_CH3_VINTAGE_JOIN_NOT_CERTIFIED


def test_ch3_suppressed_when_fina_indicator_has_no_comparable_date() -> None:
    """If ``fina_indicator`` exposes no comparable date at all, condition 3
    can never be satisfied and CH3 must not be emitted for any period.

    CONSTRUCTED ``fina_indicator`` row with no ``ann_date`` field (real
    income/balancesheet otherwise). Because the real endpoint *does* expose
    ``ann_date``, this branch is not the live behavior, but it is the frozen
    fail-closed fallback and is exercised explicitly.
    """
    income = payload("income", ts_code="000001.SZ", period="20220930")
    balancesheet = payload("balancesheet", ts_code="000001.SZ", period="20220930")
    fina = make_payload(
        [
            {
                "ts_code": "000001.SZ",
                "end_date": "20220930",
                "profit_dedt": 36_597_000_000.0,
            }
        ]
    )
    facts, uncertain = F.assemble_ch3_facts(
        income,
        balancesheet,
        fina,
        period_end=pd.Timestamp("2022-09-30"),
        retrieved_at=RETRIEVED_AT,
    )
    assert facts.empty
    assert len(uncertain) == 1
    assert uncertain.iloc[0][F.REASON_COL] == F.REASON_CH3_VINTAGE_JOIN_NOT_CERTIFIED


# ===========================================================================
# Observation-level uncertainty (Patch 3)
# ===========================================================================
def test_uncertain_observations_cases_c_d_e_exactly_one_row_per_case() -> None:
    """A constructed fixture set with one row each of cases C, D and E
    returns exactly one uncertainty row per case, keyed correctly."""
    constructed = make_payload(
        [
            {
                "ts_code": "000001.SZ",
                "ann_date": "20190430",
                "f_ann_date": None,
                "end_date": "2022-09-30",
                "report_type": "4",
                "total_revenue": 1.0,
            },
            {
                "ts_code": "000001.SZ",
                "ann_date": "20220427",
                "f_ann_date": "bad-date",
                "end_date": "2022-09-30",
                "report_type": "1",
                "total_revenue": 2.0,
            },
            {
                "ts_code": "000001.SZ",
                "ann_date": None,
                "f_ann_date": None,
                "end_date": "2022-09-30",
                "report_type": "1",
                "total_revenue": 3.0,
            },
        ]
    )
    transport = replay_transport(
        {
            fixture_key(
                "income", {"ts_code": "000001.SZ", "period": "20220930"}
            ): (200, {"code": 0, "data": constructed})
        }
    )
    isolated = ProxyTushareClient(transport=transport)
    uncertain = F.get_uncertain_observations(
        isolated,
        ["000001.SZ"],
        "2022-09-30",
        "2022-09-30",
        ["total_revenue"],
        retrieved_at=RETRIEVED_AT,
    )
    assert len(uncertain) == 3
    assert set(uncertain[F.REASON_COL]) == {
        F.REASON_INELIGIBLE_REPORT_TYPE,
        F.REASON_F_ANN_DATE_MALFORMED,
        F.REASON_BOTH_DATES_MISSING,
    }
    assert (uncertain[FIELD_COL] == "total_revenue").all()
    assert (uncertain[STOCK_COL] == "000001.SZ").all()
    assert (
        uncertain[REPORT_PERIOD_END_COL] == pd.Timestamp("2022-09-30")
    ).all()
    assert pd.api.types.is_datetime64_any_dtype(uncertain[F.RETRIEVED_AT_COL])


def test_uncertain_observations_shape_and_columns(
    client: ProxyTushareClient,
) -> None:
    uncertain = adapter(client, ["600518.SH"]).get_uncertain_observations(
        "2017-12-31", "2017-12-31", [F.CH3_FIELD]
    )
    for column in (
        STOCK_COL,
        REPORT_PERIOD_END_COL,
        FIELD_COL,
        F.REPORT_TYPE_COL,
        F.RAW_ANN_DATE_COL,
        F.RAW_F_ANN_DATE_COL,
        F.REASON_COL,
        F.RETRIEVED_AT_COL,
    ):
        assert column in uncertain.columns


def test_uncertain_observations_empty_when_all_facts_certified(
    client: ProxyTushareClient,
) -> None:
    uncertain = adapter(client, ["000001.SZ"]).get_uncertain_observations(
        "2022-06-30", "2022-06-30", ["total_revenue"]
    )
    assert uncertain.empty


# ===========================================================================
# Gate-1 reconciliation fixture (P4DB-9 evidence)
# ===========================================================================
def test_gate1_fy2022_cumulative_reconciliation_exact_match() -> None:
    """The raw facts P4DB-9 needs for the Gate-1 reconciliation: FY2022
    cumulative Q3 minus cumulative H1 equals the independently-tagged
    report_type=2 single-quarter record, exactly."""
    q3 = rows(
        "income", ts_code="000001.SZ", period="20220930", report_type="1"
    )[0]
    h1 = rows(
        "income", ts_code="000001.SZ", period="20220630", report_type="1"
    )[0]
    single = rows(
        "income", ts_code="000001.SZ", period="20220930", report_type="2"
    )[0]
    assert float(q3["total_revenue"]) - float(h1["total_revenue"]) == float(
        single["total_revenue"]
    ) == 46_243_000_000.0
    assert float(q3["n_income"]) - float(h1["n_income"]) == float(
        single["n_income"]
    ) == 14_571_000_000.0


def test_gate1_fy2022_four_quarters_retrievable(
    client: ProxyTushareClient,
) -> None:
    """P4DB-9's reconciliation fixture covers all four FY2022 quarters."""
    frame = adapter(client, ["000001.SZ"]).get_fundamentals(
        "2022-03-31", "2022-03-31", ["total_revenue"]
    )
    assert len(frame) == 1
    frame = adapter(client, ["000001.SZ"]).get_fundamentals(
        "2022-06-30", "2022-06-30", ["total_revenue"]
    )
    assert len(frame) == 1
    frame = adapter(client, ["000001.SZ"]).get_fundamentals(
        "2022-09-30", "2022-09-30", ["total_revenue"]
    )
    assert len(frame) == 1
    frame = adapter(client, ["000001.SZ"]).get_fundamentals(
        "2022-12-31", "2022-12-31", ["total_revenue"]
    )
    assert len(frame) == 1


# ===========================================================================
# T4 < T3 adversarial chain raw facts (documentation/evidence only)
# ===========================================================================
def test_t1_lt_t2_lt_t3_chain_raw_facts_retrievable(
    client: ProxyTushareClient,
) -> None:
    """600518.SH Q1/H1/Q3 2018 raw facts are each retrievable with the
    correct knowledge_date. The adapter performs no subtraction; a future
    consumer uses these to avoid the t1 < t2 < t3 look-ahead trap."""
    q1 = adapter(client, ["600518.SH"]).get_fundamentals(
        "2018-03-31", "2018-03-31", ["total_revenue"]
    )
    h1 = adapter(client, ["600518.SH"]).get_fundamentals(
        "2018-06-30", "2018-06-30", ["total_revenue"]
    )
    q3 = adapter(client, ["600518.SH"]).get_fundamentals(
        "2018-09-30", "2018-09-30", ["total_revenue"]
    )
    q1_original = q1[q1[F.REPORT_TYPE_COL] == "1"].iloc[0]
    assert q1_original[KNOWLEDGE_DATE_COL] == pd.Timestamp("2018-04-28")
    h1_original = h1[h1[F.REPORT_TYPE_COL] == "1"].iloc[0]
    assert h1_original[KNOWLEDGE_DATE_COL] == pd.Timestamp("2018-08-29")
    assert {pd.Timestamp("2018-10-27"), pd.Timestamp("2019-10-30")} == set(
        q3[KNOWLEDGE_DATE_COL]
    )


# ===========================================================================
# Fail-closed field resolution and disclosure_date evidence
# ===========================================================================
def test_unknown_field_is_refused() -> None:
    with pytest.raises(F.TushareUnknownFieldError):
        F.resolve_field_endpoint("not_a_tushare_field")


def test_metadata_field_is_not_a_value_field() -> None:
    with pytest.raises(F.TushareUnknownFieldError):
        F.resolve_field_endpoint("ann_date")


def test_ambiguous_field_is_refused() -> None:
    # ``credit_impa_loss`` is a real field of both income and cashflow.
    with pytest.raises(F.TushareAmbiguousFieldError):
        F.resolve_field_endpoint("credit_impa_loss")


def test_disclosure_date_corroborates_600518_original_vintage() -> None:
    """Recorded ``disclosure_date`` corroborates the original vintage."""
    row = rows("disclosure_date", ts_code="600518.SH", end_date="20171231")[0]
    assert row["actual_date"] == "20180426"


def test_disclosure_date_corroborates_002450_original_vintage() -> None:
    row = rows("disclosure_date", ts_code="002450.SZ", end_date="20171231")[0]
    assert row["actual_date"] == "20180420"
