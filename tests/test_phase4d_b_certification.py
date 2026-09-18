"""Phase 4D-B Tushare certification gate (task P4DB-9).

This is not an ordinary unit-test module. It is the executable half of the
checked-in certification report ``docs/phase4d_b_tushare_certification.md``.
Every disposition line in that document must correspond to a real
assertion in this file against the real, assembled
:class:`~smart_beta.vendors.tushare.source.TushareAShareSource`; the
markdown-presence check at the bottom exists only as a synchronization
guard alongside these executable checks, never as the sole proof of a
claim.

The 16 required checks are the ones in
``worker_tasks/phase4d_b/task-p4db-9-certification.md``'s "Required
certification report content" — the single normative source for the list.
The four frozen values (items 3, 4, 13, 14) may never be upgraded by any
fixture result; this file asserts them exactly.

Evidence discipline: every specimen under ``tests/fixtures/tushare/`` is
either **proxy-observed** (P4DB-4 through P4DB-7, recorded through
``pcd.mobcvb.cn``) or **contract-modeled** (P4DB-1 and P4DB-2, Wave 1 had
no live credential). The two are never conflated below. Some
knowledge-date branches and no-real-divergence branches are exercised with
explicitly-labeled **constructed** payloads inline; no real specimen is
substituted where one exists.

Run with ``.venv/bin/pytest tests/test_phase4d_b_certification.py``.
"""

from __future__ import annotations

import json
import re
import urllib.request
from collections import OrderedDict
from pathlib import Path
from typing import Mapping, Sequence

import pandas as pd
import pytest

from smart_beta.pit.compliance import (
    check_deterministic_results,
    check_fundamentals_vintages_preserved,
    check_schema_conformance,
)
from smart_beta.pit.schema import (
    FIELD_COL,
    IS_RESTATEMENT_COL,
    KNOWLEDGE_DATE_COL,
    REPORT_PERIOD_END_COL,
    STOCK_COL,
    VALUE_COL,
)
from smart_beta.vendors.tushare import fundamentals as F
from smart_beta.vendors.tushare.client import (
    TushareAPIError,
    TushareNonDeterministicResponseError,
)
from smart_beta.vendors.tushare.corporate_actions import (
    map_adj_factor_to_corporate_actions,
)
from smart_beta.vendors.tushare.fundamentals import (
    TushareConflictingVintageError,
    map_statement_payload,
)
from smart_beta.vendors.tushare.identifiers import resolve_stock_id
from smart_beta.vendors.tushare.market_data import (
    FLOAT_MARKET_CAP_CERTIFICATION_STATUS,
    FLOAT_MARKET_CAP_IS_DIAGNOSTIC_ONLY,
    TOTAL_MARKET_CAP_CERTIFICATION_STATUS,
)
from smart_beta.vendors.tushare.proxy_client import check_canonical_consistency
from smart_beta.vendors.tushare.source import TushareAShareSource

# ---------------------------------------------------------------------------
# Paths / constants
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent.parent
_FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "tushare"
_MD_DIR = _FIXTURE_ROOT / "market_data"
_CA_DIR = _FIXTURE_ROOT / "corporate_actions"
_LI_DIR = _FIXTURE_ROOT / "listing"
_FU_DIR = _FIXTURE_ROOT / "fundamentals"
_CL_DIR = _FIXTURE_ROOT / "client"
_ID_DIR = _FIXTURE_ROOT / "identifiers"
_REPORT_PATH = _REPO_ROOT / "docs" / "phase4d_b_tushare_certification.md"

_RETRIEVED_AT = pd.Timestamp("2026-09-17T00:00:00")
_PINGAN = "000001.SZ"


# ---------------------------------------------------------------------------
# Offline real-fixture harness (mirrors tests/test_tushare_source.py)
# ---------------------------------------------------------------------------
def _unwrap(body: object) -> dict:
    """Return the ``fields``/``items`` payload from a recorded body."""
    assert isinstance(body, dict)
    data = body.get("data")
    if isinstance(data, dict) and "fields" in data:
        return data
    return body


def _payload_rows(payload: Mapping) -> tuple[list[str], list[dict]]:
    fields = list(payload.get("fields") or [])
    return fields, [dict(zip(fields, item)) for item in payload.get("items") or []]


def _iter_recorded(dirpath: Path):
    manifest = json.loads((dirpath / "manifest.json").read_text(encoding="utf-8"))
    recordings = manifest.get("recordings", manifest)
    for filename, entry in recordings.items():
        if not isinstance(entry, dict) or "api_name" not in entry:
            continue
        payload = _unwrap(json.loads((dirpath / filename).read_text(encoding="utf-8")))
        yield entry["api_name"], dict(entry.get("params") or {}), payload


def _filters_rows(rows: list[dict], params: Mapping[str, str]) -> list[dict]:
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

    Serves the real, recorded rows for the security regardless of the exact
    lookback/corroboration window the assembled source chooses, then applies
    the requested date filter -- exactly the pattern ``test_tushare_source.py``
    established. Statement requests dispatch on
    ``(api_name, ts_code, period[, report_type])``; ``adj_factor`` history is
    loaded too (the assembled source does not consume it, but item 9's
    cross-check does, through the same client).
    """

    def __init__(self) -> None:
        self._daily: dict[str, OrderedDict[str, dict]] = {}
        self._daily_basic: dict[str, OrderedDict[str, dict]] = {}
        self._stk_limit: dict[str, OrderedDict[str, dict]] = {}
        self._adj_factor: dict[str, OrderedDict[str, dict]] = {}
        self._fields: dict[tuple[str, str], list[str]] = {}
        self._suspend: dict[str, list[dict]] = {}
        self._dividend: dict[str, list[dict]] = {}
        self._stock_basic: dict[str, list[dict]] = {}
        self._statements: dict[tuple[str, str, str], dict] = {}
        self._by_report_type: dict[tuple[str, str, str, str], dict] = {}
        self.calls: list[tuple[str, dict]] = []
        self._load()

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
                elif api_name == "adj_factor":
                    self._fields[("adj_factor", ts_code)] = fields
                    store = self._adj_factor.setdefault(ts_code, OrderedDict())
                    for row in rows:
                        store.setdefault(row["trade_date"], row)
                elif api_name == "suspend_d":
                    self._suspend.setdefault(ts_code, []).extend(rows)
                elif api_name == "dividend":
                    self._fields[("dividend", ts_code)] = fields
                    self._dividend.setdefault(ts_code, []).extend(rows)

        for api_name, params, payload in _iter_recorded(_LI_DIR):
            ts_code = params.get("ts_code")
            if ts_code is None:
                continue
            if api_name == "daily":
                fields, rows = _payload_rows(payload)
                self._fields[("daily", ts_code)] = fields
                store = self._daily.setdefault(ts_code, OrderedDict())
                for row in rows:
                    store.setdefault(row["trade_date"], row)
            elif api_name == "stock_basic":
                _, rows = _payload_rows(payload)
                self._stock_basic.setdefault(ts_code, []).extend(rows)

        for api_name, params, payload in _iter_recorded(_FU_DIR):
            ts_code = params.get("ts_code")
            period = params.get("period")
            if ts_code is None or period is None:
                continue
            report_type = params.get("report_type")
            if report_type is not None:
                self._by_report_type[
                    (api_name, ts_code, period, str(report_type))
                ] = payload
            else:
                self._statements.setdefault((api_name, ts_code, period), payload)

    def _dated_payload(self, api_name: str, ts_code: str, rows: Sequence[dict]) -> dict:
        fields = self._fields.get((api_name, ts_code))
        if fields is None:
            fields = list(rows[0].keys()) if rows else []
        return {
            "fields": fields,
            "items": [[row.get(f) for f in fields] for row in rows],
        }

    def fetch(self, api_name: str, *, retry_on_empty: bool = False, **params: str) -> dict:
        self.calls.append((api_name, dict(params)))
        ts_code = params.get("ts_code", "")

        if api_name in ("daily", "daily_basic", "stk_limit", "adj_factor"):
            store = {
                "daily": self._daily,
                "daily_basic": self._daily_basic,
                "stk_limit": self._stk_limit,
                "adj_factor": self._adj_factor,
            }[api_name]
            rows = list(store.get(ts_code, OrderedDict()).values())
            return self._dated_payload(api_name, ts_code, _filters_rows(rows, params))
        if api_name == "suspend_d":
            return self._dated_payload(api_name, ts_code, self._suspend.get(ts_code, []))
        if api_name == "dividend":
            return self._dated_payload(api_name, ts_code, self._dividend.get(ts_code, []))
        if api_name == "stock_basic":
            rows = self._stock_basic.get(ts_code, [])
            fields = list(rows[0].keys()) if rows else []
            return {
                "fields": fields,
                "items": [[row.get(f) for f in fields] for row in rows],
            }

        period = params.get("period")
        report_type = params.get("report_type")
        if report_type is not None:
            payload = self._by_report_type.get(
                (api_name, ts_code, period, str(report_type))
            )
            if payload is not None:
                return payload
        payload = self._statements.get((api_name, ts_code, period))
        if payload is not None:
            return payload
        return {"fields": [], "items": []}


class _OverrideClient(_FixtureClient):
    """Fixture client that returns constructed payloads for specific requests.

    Used only for the knowledge-date branches (P4DB-6 cases B/C/D/E and the
    condition-3 divergence) for which no real specimen exists; every override
    is labeled constructed at the call site.
    """

    def __init__(self, overrides: Mapping[tuple[str, str, str], dict]) -> None:
        self._overrides = dict(overrides)
        super().__init__()

    def fetch(self, api_name: str, *, retry_on_empty: bool = False, **params: str) -> dict:
        key = (api_name, params.get("ts_code", ""), params.get("period", ""))
        if key in self._overrides:
            self.calls.append((api_name, dict(params)))
            return self._overrides[key]
        return super().fetch(api_name, retry_on_empty=retry_on_empty, **params)


class _CapEnforcingClient(_FixtureClient):
    """Fixture client that enforces the proxy's real <=366-day range cap."""

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


@pytest.fixture
def client() -> _FixtureClient:
    return _FixtureClient()


def _source(ts_codes: Sequence[str], client: _FixtureClient) -> TushareAShareSource:
    return TushareAShareSource(list(ts_codes), client)


@pytest.fixture(autouse=True)
def _forbid_live_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*args: object, **kwargs: object) -> object:
        raise AssertionError(
            "Test attempted a live network call; inject a replay transport."
        )

    monkeypatch.setattr(urllib.request, "urlopen", _boom)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def _sole(frame: pd.DataFrame, field: str) -> pd.Series:
    matched = frame[frame[FIELD_COL] == field]
    assert len(matched) == 1, f"expected exactly one {field!r} fact, got {len(matched)}"
    return matched.iloc[0]


# ===========================================================================
# [1] SCHEMA CONFORMANCE
# ===========================================================================
def test_item01_schema_conformance_all_seven_methods(client: _FixtureClient) -> None:
    source = _source([_PINGAN], client)
    results = check_schema_conformance(
        source, "2022-06-30", "2022-09-30", ["total_revenue"]
    )
    failures = [r for r in results if not r.passed]
    assert not failures, "\n".join(r.message for r in failures)
    assert len(results) == 7
    assert {r.name for r in results} == {
        "schema_conformance_trading_calendar",
        "schema_conformance_get_raw_returns",
        "schema_conformance_get_corporate_actions",
        "schema_conformance_get_market_cap",
        "schema_conformance_get_fundamentals",
        "schema_conformance_get_trading_status",
        "schema_conformance_get_listing_info",
    }


# ===========================================================================
# [2] CUMULATIVE VS SINGLE-QUARTER RECONCILIATION
# ===========================================================================
def test_item02_cumulative_single_quarter_reconciliation(client: _FixtureClient) -> None:
    source = _source([_PINGAN], client)
    q3 = source.get_fundamentals(
        "2022-09-30", "2022-09-30", ["total_revenue", "n_income"]
    )
    h1 = source.get_fundamentals(
        "2022-06-30", "2022-06-30", ["total_revenue", "n_income"]
    )
    q3_revenue = float(_sole(q3, "total_revenue")[VALUE_COL])
    h1_revenue = float(_sole(h1, "total_revenue")[VALUE_COL])
    q3_income = float(_sole(q3, "n_income")[VALUE_COL])
    h1_income = float(_sole(h1, "n_income")[VALUE_COL])
    assert q3_revenue == pytest.approx(138_265_000_000.0)
    assert h1_revenue == pytest.approx(92_022_000_000.0)
    assert q3_income == pytest.approx(36_659_000_000.0)
    assert h1_income == pytest.approx(22_088_000_000.0)

    single_payload = client.fetch(
        "income", ts_code=_PINGAN, period="20220930", report_type="2"
    )
    single, _ = map_statement_payload(
        single_payload, "income", ["total_revenue", "n_income"], retrieved_at=_RETRIEVED_AT
    )
    single_revenue = float(_sole(single, "total_revenue")[VALUE_COL])
    single_income = float(_sole(single, "n_income")[VALUE_COL])
    assert single_revenue == pytest.approx(46_243_000_000.0)
    assert single_income == pytest.approx(14_571_000_000.0)

    assert q3_revenue - h1_revenue == single_revenue
    assert q3_income - h1_income == single_income
    # Anti-tautology: the single-quarter figure is materially different from
    # the cumulative legs, so the arithmetic is not trivially zero.
    assert single_revenue < h1_revenue < q3_revenue


# ===========================================================================
# [3] CHINA FUNDAMENTALS VINTAGE CAPABILITY
# ===========================================================================
def test_item03_vintage_capability_600518_and_002450(client: _FixtureClient) -> None:
    # 600518.SH FY2017: both real vintages survive the assembled pipeline.
    kweichow = _source(["600518.SH"], client)
    results = check_fundamentals_vintages_preserved(
        kweichow,
        stock_id="600518.SH",
        report_period_end="2017-12-31",
        field="total_revenue",
        expected_knowledge_dates=("2018-04-26", "2019-04-30"),
    )
    assert all(r.passed for r in results), "\n".join(r.message for r in results)
    frame = kweichow.get_fundamentals("2017-12-31", "2017-12-31", ["total_revenue"])
    by_date = frame.set_index(KNOWLEDGE_DATE_COL)
    assert float(by_date.loc[pd.Timestamp("2018-04-26"), VALUE_COL]) == pytest.approx(
        26_476_970_977.57
    )
    assert float(by_date.loc[pd.Timestamp("2019-04-30"), VALUE_COL]) == pytest.approx(
        17_578_618_640.06
    )
    assert bool(by_date.loc[pd.Timestamp("2018-04-26"), IS_RESTATEMENT_COL]) is False
    assert bool(by_date.loc[pd.Timestamp("2019-04-30"), IS_RESTATEMENT_COL]) is True

    # 002450.SZ FY2015: two distinct-value vintages collapse onto one schema
    # key; the assembled source refuses to guess and raises loudly.
    kangde = _source(["002450.SZ"], client)
    with pytest.raises(TushareConflictingVintageError) as excinfo:
        kangde.get_fundamentals("2015-12-31", "2015-12-31", ["total_revenue"])
    assert excinfo.value.key == (
        "002450.SZ",
        pd.Timestamp("2015-12-31"),
        "total_revenue",
        pd.Timestamp("2021-02-28"),
    )


# ===========================================================================
# [4] CHINA FUNDAMENTALS VINTAGE COVERAGE COMPLETENESS
# ===========================================================================
def test_item04_vintage_coverage_counterexample(client: _FixtureClient) -> None:
    """002069.SZ FY2017 is the standing coverage counterexample: one real
    retrievable vintage despite a documented reprocessing (ann != f_ann)."""
    source = _source(["002069.SZ"], client)
    frame = source.get_fundamentals("2017-12-31", "2017-12-31", ["total_revenue"])
    assert len(frame) == 1
    row = frame.iloc[0]
    assert row[REPORT_PERIOD_END_COL] == pd.Timestamp("2017-12-31")
    assert float(row[VALUE_COL]) == pytest.approx(3_205_845_988.9)
    assert row[KNOWLEDGE_DATE_COL] == pd.Timestamp("2020-10-15")
    # The single row is itself flagged as in-place reprocessed -- so the one
    # vintage is NOT evidence that no restatement occurred.
    assert bool(row[IS_RESTATEMENT_COL]) is True
    assert frame[KNOWLEDGE_DATE_COL].nunique() == 1


# ===========================================================================
# [5] KNOWLEDGE-DATE RULE
# ===========================================================================
def test_item05a_knowledge_date_real_specimens(client: _FixtureClient) -> None:
    """Real case A (000001.SZ) and real divergent case A (002450.SZ)."""
    pingan = _source([_PINGAN], client)
    case_a = pingan.get_fundamentals("2022-06-30", "2022-06-30", ["total_revenue"])
    assert len(case_a) == 1
    assert case_a.iloc[0][KNOWLEDGE_DATE_COL] == pd.Timestamp("2022-08-18")

    # The decisive stale-ann_date specimen. `ass_invest_income` is identical
    # across the report_type=1 and report_type=4 FY2015 rows, so the
    # assembled source can emit it instead of hitting the total_revenue
    # conflicting-vintage refusal -- and it must resolve to the 2021
    # f_ann_date, never the stale 2016 ann_date.
    kangde = _source(["002450.SZ"], client)
    frame = kangde.get_fundamentals("2015-12-31", "2015-12-31", ["ass_invest_income"])
    by_date = frame.set_index(KNOWLEDGE_DATE_COL)
    assert bool(
        (frame[F.VINTAGE_ID_COL].astype(str).str.contains("1|4")).any()
    )
    late = by_date.loc[pd.Timestamp("2021-02-28")]
    assert float(late[VALUE_COL]) == pytest.approx(-1_871_820.0)
    assert pd.Timestamp("2016-04-22") in by_date.index
    # The report_type=5 original is the 2016-dated row; the report_type=1 row
    # never appears at the stale 2016 ann_date.
    assert str(by_date.loc[pd.Timestamp("2016-04-22"), F.REPORT_TYPE_COL]) != "1"

    # Independent raw-row assertion on the same real recorded specimen.
    raw = client.fetch("income", ts_code="002450.SZ", period="20151231")
    _, rows = _payload_rows(raw)
    type1 = next(
        r for r in rows if str(r.get("report_type")).strip() == "1"
    )
    assert type1["ann_date"] == "20160422"
    assert type1["f_ann_date"] == "20210228"
    assert F.knowledge_date_decision(type1).knowledge_date == pd.Timestamp(
        "2021-02-28"
    )


def test_item05b_fallback_case_b_fires() -> None:
    """CONSTRUCTED case B: missing f_ann_date, eligible report_type, so the
    fallback fires and uses ann_date -- through the assembled source."""
    payload = {
        "fields": ["ts_code", "ann_date", "f_ann_date", "end_date", "report_type", "total_revenue"],
        "items": [[_PINGAN, "20220427", None, "20220331", "1", 123.0]],
    }
    source = _source([_PINGAN], _OverrideClient(
        {("income", _PINGAN, "20220331"): payload}
    ))
    frame = source.get_fundamentals("2022-03-31", "2022-03-31", ["total_revenue"])
    assert len(frame) == 1
    assert frame.iloc[0][KNOWLEDGE_DATE_COL] == pd.Timestamp("2022-04-27")
    assert float(frame.iloc[0][VALUE_COL]) == pytest.approx(123.0)


def test_item05c_fallback_case_c_refuses() -> None:
    """CONSTRUCTED case C: missing f_ann_date on an ineligible report_type
    (4) is dropped, never defaulted, and recorded."""
    payload = {
        "fields": ["ts_code", "ann_date", "f_ann_date", "end_date", "report_type", "total_revenue"],
        "items": [[_PINGAN, "20190430", None, "20220331", "4", 1.0]],
    }
    source = _source([_PINGAN], _OverrideClient(
        {("income", _PINGAN, "20220331"): payload}
    ))
    assert source.get_fundamentals("2022-03-31", "2022-03-31", ["total_revenue"]).empty
    uncertain = source.get_uncertain_observations(
        "2022-03-31", "2022-03-31", ["total_revenue"]
    )
    assert (uncertain[F.REASON_COL] == F.REASON_INELIGIBLE_REPORT_TYPE).any()


def test_item05d_case_d_malformed() -> None:
    """CONSTRUCTED case D: a present-but-malformed f_ann_date is never
    coerced, dropped, and recorded."""
    payload = {
        "fields": ["ts_code", "ann_date", "f_ann_date", "end_date", "report_type", "total_revenue"],
        "items": [[_PINGAN, "20220427", "not-a-date", "20220331", "1", 2.0]],
    }
    source = _source([_PINGAN], _OverrideClient(
        {("income", _PINGAN, "20220331"): payload}
    ))
    assert source.get_fundamentals("2022-03-31", "2022-03-31", ["total_revenue"]).empty
    uncertain = source.get_uncertain_observations(
        "2022-03-31", "2022-03-31", ["total_revenue"]
    )
    assert (uncertain[F.REASON_COL] == F.REASON_F_ANN_DATE_MALFORMED).any()


def test_item05e_case_e_both_missing() -> None:
    """CONSTRUCTED case E: neither date usable -> dropped and recorded."""
    payload = {
        "fields": ["ts_code", "ann_date", "f_ann_date", "end_date", "report_type", "total_revenue"],
        "items": [[_PINGAN, None, "", "20220331", "1", 3.0]],
    }
    source = _source([_PINGAN], _OverrideClient(
        {("income", _PINGAN, "20220331"): payload}
    ))
    assert source.get_fundamentals("2022-03-31", "2022-03-31", ["total_revenue"]).empty
    uncertain = source.get_uncertain_observations(
        "2022-03-31", "2022-03-31", ["total_revenue"]
    )
    assert (uncertain[F.REASON_COL] == F.REASON_BOTH_DATES_MISSING).any()


# ===========================================================================
# [6] CH3 NI-EX-NONRECURRING
# ===========================================================================
def test_item06a_ch3_suppressed_600518(client: _FixtureClient) -> None:
    source = _source(["600518.SH"], client)
    frame = source.get_fundamentals("2017-12-31", "2017-12-31", [F.CH3_FIELD])
    assert frame.empty
    uncertain = source.get_uncertain_observations(
        "2017-12-31", "2017-12-31", [F.CH3_FIELD]
    )
    entry = uncertain[uncertain[FIELD_COL] == F.CH3_FIELD]
    assert len(entry) == 1
    assert entry.iloc[0][F.REASON_COL] == F.REASON_CH3_VINTAGE_JOIN_NOT_CERTIFIED


def test_item06b_ch3_suppressed_002450(client: _FixtureClient) -> None:
    source = _source(["002450.SZ"], client)
    frame = source.get_fundamentals("2015-12-31", "2015-12-31", [F.CH3_FIELD])
    assert frame.empty
    uncertain = source.get_uncertain_observations(
        "2015-12-31", "2015-12-31", [F.CH3_FIELD]
    )
    entry = uncertain[uncertain[FIELD_COL] == F.CH3_FIELD]
    assert len(entry) == 1
    assert entry.iloc[0][F.REASON_COL] == F.REASON_CH3_VINTAGE_JOIN_NOT_CERTIFIED


def test_item06c_ch3_successful_join(client: _FixtureClient) -> None:
    source = _source([_PINGAN], client)
    frame = source.get_fundamentals("2022-09-30", "2022-09-30", [F.CH3_FIELD])
    assert len(frame) == 1
    row = frame.iloc[0]
    assert row[FIELD_COL] == F.CH3_FIELD
    assert float(row[VALUE_COL]) == pytest.approx(36_597_000_000.0)
    assert row[KNOWLEDGE_DATE_COL] == pd.Timestamp("2022-10-25")
    assert row[F.REPORTING_BASIS_COL] == F.CUMULATIVE_YTD
    assert bool(row[IS_RESTATEMENT_COL]) is False


# ===========================================================================
# [7] TOTAL / FLOAT MARKET CAP
# ===========================================================================
def test_item07_total_market_cap_canonical(client: _FixtureClient) -> None:
    source = _source([_PINGAN], client)
    frame = source.get_market_cap("2013-06-20", "2013-06-20")
    assert len(frame) == 1
    row = frame.iloc[0]

    raw = client.fetch(
        "daily_basic", ts_code=_PINGAN, start_date="20130620", end_date="20130620"
    )
    _, daily_basic_rows = _payload_rows(raw)
    day = next(r for r in daily_basic_rows if r["trade_date"] == "20130620")
    assert float(row["total_mcap"]) == pytest.approx(float(day["total_mv"]))
    assert float(row["float_mcap"]) == pytest.approx(float(day["circ_mv"]))
    assert float(row["total_mcap"]) != float(row["float_mcap"])
    assert float(row["_total_mcap_sanity_ratio"]) == pytest.approx(1.0, abs=1e-6)

    assert FLOAT_MARKET_CAP_IS_DIAGNOSTIC_ONLY is True
    assert TOTAL_MARKET_CAP_CERTIFICATION_STATUS.startswith(
        "TOTAL MARKET CAP = CANONICAL"
    )
    assert (
        FLOAT_MARKET_CAP_CERTIFICATION_STATUS
        == "FLOAT MARKET CAP = DIAGNOSTIC ONLY, NOT INDEPENDENTLY CERTIFIED"
    )


# ===========================================================================
# [8] CH4 TURNOVER FEASIBILITY (mechanical only)
# ===========================================================================
def test_item08_ch4_turnover_feasibility_mechanical_only(client: _FixtureClient) -> None:
    source = _source([_PINGAN], client)
    # Two sub-windows keep each request inside the proxy's <=366-day cap.
    first = source.get_market_cap("2013-01-01", "2013-12-31")
    second = source.get_market_cap("2014-01-01", "2014-03-31")
    market_cap = pd.concat([first, second], ignore_index=True)
    assert len(market_cap) == 295
    assert int(market_cap["_total_share"].notna().sum()) == 251
    assert sorted(market_cap["_total_share"].dropna().unique().tolist()) == [
        512335.0,
        819736.0,
        952075.0,
    ]

    daily_parts = [
        client.fetch("daily", ts_code=_PINGAN, start_date="20130101", end_date="20131231"),
        client.fetch("daily", ts_code=_PINGAN, start_date="20140101", end_date="20140331"),
    ]
    volume: dict[str, float] = {}
    for part in daily_parts:
        _, rows = _payload_rows(part)
        for r in rows:
            raw = r.get("vol")
            if raw not in (None, ""):
                volume[r["trade_date"]] = float(raw)

    shares = {
        ts.strftime("%Y%m%d"): float(share)
        for ts, share in zip(
            market_cap["date"], market_cap["_total_share"]
        )
        if pd.notna(share)
    }
    usable = [d for d in shares if d in volume]
    assert len(usable) == 251
    turnover = [volume[d] / shares[d] for d in usable]
    assert all(t > 0 and pd.notna(t) for t in turnover)
    # Mechanical feasibility only -- total_share demonstrably steps within the
    # window, so this is explicitly NOT a PIT-immutability certification.
    assert len(set(shares.values())) == 3


# ===========================================================================
# [9] CHANGING ADJ_FACTOR RECONSTRUCTION
# ===========================================================================
def test_item09_changing_adj_factor_2013_06_20(client: _FixtureClient) -> None:
    source = _source([_PINGAN], client)

    market_cap = source.get_market_cap("2013-06-01", "2013-07-01")
    by_date = market_cap.set_index("date")
    before = float(by_date.loc[pd.Timestamp("2013-06-18"), "_total_share"])
    at = float(by_date.loc[pd.Timestamp("2013-06-20"), "_total_share"])
    assert before == pytest.approx(512335.0)
    assert at == pytest.approx(819736.0)
    assert at / before == pytest.approx(1.6)

    actions = source.get_corporate_actions("2013-06-01", "2013-07-01")
    action = actions[actions["effective_date"] == pd.Timestamp("2013-06-20")]
    assert len(action) == 1
    assert action.iloc[0]["action_type"] == "split_dividend"
    frozen_factor = float(action.iloc[0]["adjustment_factor"])
    assert frozen_factor == pytest.approx(1.614263, abs=1e-6)

    # Vendor adj_factor cross-check through the same real client + P4DB-5's
    # public mapper (the assembled source does not surface adj_factor).
    adj_payload = client.fetch(
        "adj_factor", ts_code=_PINGAN, start_date="20130101", end_date="20131231"
    )
    steps = map_adj_factor_to_corporate_actions(adj_payload)
    step = steps[steps["effective_date"] == pd.Timestamp("2013-06-20")]
    assert len(step) == 1
    assert float(step.iloc[0]["_prev_adj_factor"]) == pytest.approx(36.173)
    assert float(step.iloc[0]["_new_adj_factor"]) == pytest.approx(58.387)
    vendor_ratio = float(step.iloc[0]["_new_adj_factor"]) / float(
        step.iloc[0]["_prev_adj_factor"]
    )
    assert vendor_ratio == pytest.approx(frozen_factor, rel=1e-3)


# ===========================================================================
# [10] IDENTIFIER CONTINUITY
# ===========================================================================
def test_item10_identifier_continuity_not_certified(client: _FixtureClient) -> None:
    ordinary = resolve_stock_id("000001.SZ")
    assert ordinary.stock_id == "000001.SZ"
    assert ordinary.is_permanent is True

    terminal = resolve_stock_id(
        "002450.SZ",
        {"ts_code": "002450.SZ", "list_status": "D", "delist_date": "20210531"},
    )
    assert terminal.stock_id == "002450.SZ"
    assert terminal.is_permanent is False

    # The 000024.SZ/001914.SZ restructuring pair: two independent codes, no
    # vendor-asserted old-code -> new-code join.
    constructed = json.loads(
        (_ID_DIR / "constructed_stock_basic.json").read_text(encoding="utf-8")
    )
    constructed = constructed.get("data", constructed)
    _, rows = _payload_rows(constructed)
    row_000024 = next(r for r in rows if r["ts_code"] == "000024.SZ")
    row_001914 = next(r for r in rows if r["ts_code"] == "001914.SZ")
    left = resolve_stock_id("000024.SZ", row_000024)
    right = resolve_stock_id("001914.SZ", row_001914)
    assert left.stock_id != right.stock_id
    assert left.is_permanent is False  # delisted/stale terminal signal
    # The namechange endpoint is keyed by the same ts_code it describes, so it
    # cannot express a code change; this module exposes no such mapping.
    import smart_beta.vendors.tushare.identifiers as identifiers_mod

    assert identifiers_mod.NAME_CHANGE_FIELD == "name"
    assert not hasattr(identifiers_mod, "successor_stock_id")

    # The assembled source's listing path resolves the same way.
    source = _source(["002450.SZ"], client)
    listing = source.get_listing_info()
    assert listing.iloc[0][STOCK_COL] == "002450.SZ"


# ===========================================================================
# [11] DELISTING CORROBORATION
# ===========================================================================
def test_item11_delisting_corroboration_002450(client: _FixtureClient) -> None:
    source = _source(["002450.SZ"], client)
    listing = source.get_listing_info()
    assert len(listing) == 1
    assert listing.iloc[0]["delist_date"] == pd.Timestamp("2021-05-31")
    assert listing.iloc[0]["_delist_corroboration"] == "corroborated"

    control = _source(["000001.SZ"], _FixtureClient()).get_listing_info()
    assert pd.isna(control.iloc[0]["delist_date"])
    assert control.iloc[0]["_delist_corroboration"] == "no_delist_claim"


# ===========================================================================
# [12] PROXY DETERMINISM
# ===========================================================================
def _manifest(dirpath: Path) -> dict:
    return json.loads((dirpath / "manifest.json").read_text(encoding="utf-8"))


def _recordings(manifest: dict) -> dict:
    """The recording entries, for both manifest shapes in this phase.

    P4DB-4/5/6 nest them under ``recordings``; P4DB-7's manifest is flat
    (filename -> entry, plus ``_provenance``).
    """
    if "recordings" in manifest:
        return manifest["recordings"]
    return {
        key: value
        for key, value in manifest.items()
        if isinstance(value, dict) and "api_name" in value
    }


def test_item12_proxy_determinism_not_certified() -> None:
    # (a) The canonical-consistency safeguard executes on a real recorded
    # payload: identical -> returns it; differing -> named loud error.
    real = _unwrap(
        json.loads(
            (_MD_DIR / "daily_end_date-20230115_start_date-20230101_ts_code-000001.SZ.json").read_text(
                encoding="utf-8"
            )
        )
    )
    assert check_canonical_consistency("daily", {}, [real, real]) is real
    altered = json.loads(json.dumps(real))
    altered["items"][0][-1] = "changed"
    with pytest.raises(TushareNonDeterministicResponseError):
        check_canonical_consistency("daily", {}, [real, altered])

    # (b) Positive live coverage exists and is documented.
    assert _manifest(_CA_DIR)["_provenance"]["samples_per_request"] == 3
    assert "canonical-consistency" in _manifest(_FU_DIR)["_provenance"]["method"]
    assert _manifest(_MD_DIR)["_provenance"]["live_recorded"] is True
    assert _manifest(_LI_DIR)["_provenance"]["live_recorded"] is True

    # (c) ... but coverage is incomplete, which is why the cross-task item is
    # NOT CERTIFIED: P4DB-4 declares no canonical check, P4DB-7 recorded
    # large sweeps with one sample, and Wave 1 is contract-modeled.
    assert "canonical" not in json.dumps(_manifest(_MD_DIR)["_provenance"]).lower()
    listing_recordings = _recordings(_manifest(_LI_DIR))
    assert any(entry.get("samples") == 1 for entry in listing_recordings.values())
    assert _manifest(_CL_DIR)["_provenance"]["live_recorded"] is False
    assert "hand-constructed" in (_ID_DIR / "README.md").read_text(encoding="utf-8")

    # (d) No task ever reported the nondeterminism error firing.
    haystack = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in list(_FIXTURE_ROOT.rglob("*.json"))
        + list(_FIXTURE_ROOT.rglob("*.md"))
    )
    assert "TushareNonDeterministicResponseError fired" not in haystack


# ===========================================================================
# [13] PROXY SUFFICIENT FOR PHASE 4D-B POC
# ===========================================================================
def test_item13_proxy_sufficient_for_poc(client: _FixtureClient) -> None:
    # A POC-grade end-to-end run: the real assembled source satisfies schema
    # conformance and the Gate-1 reconciliation.
    source = _source([_PINGAN], client)
    assert all(
        r.passed
        for r in check_schema_conformance(
            source, "2022-06-30", "2022-09-30", ["total_revenue"]
        )
    )
    q3 = source.get_fundamentals("2022-09-30", "2022-09-30", ["total_revenue"])
    assert float(_sole(q3, "total_revenue")[VALUE_COL]) == pytest.approx(
        138_265_000_000.0
    )


# ===========================================================================
# [14] PROXY SUFFICIENT FOR PRODUCTION
# ===========================================================================
def test_item14_proxy_not_sufficient_for_production(client: _FixtureClient) -> None:
    source = _source(["000001.SZ"], client)
    facts = source.get_fundamentals("2022-06-30", "2022-06-30", ["total_revenue"])
    # Provenance risk: every fact is proxy-tagged, never official-API access.
    assert (facts[F.SOURCE_VENDOR_COL] == "Tushare").all()
    assert (facts[F.ACCESS_PATH_COL] == "proxy:pcd.mobcvb.cn").all()
    # Float market cap is diagnostic-only, not a production float figure.
    assert FLOAT_MARKET_CAP_IS_DIAGNOSTIC_ONLY is True
    # Failure mode 1: blank-out.
    kangde = _source(["002450.SZ"], _FixtureClient())
    blank = kangde.get_fundamentals("2017-12-31", "2017-12-31", ["total_revenue"])
    assert bool(blank.iloc[0][F.IS_BLANK_OUT_COL]) is True
    # Failure mode 2: stale-ann_date in-place reprocessing.
    assert bool(
        kangde.get_fundamentals("2016-12-31", "2016-12-31", ["total_revenue"]).iloc[0][
            IS_RESTATEMENT_COL
        ]
    ) is True
    # Failure mode 3: the un-chunked <=366-day cap.
    capped = _source([_PINGAN], _CapEnforcingClient())
    with pytest.raises(TushareAPIError) as excinfo:
        capped.get_market_cap("2013-01-01", "2014-03-31")
    assert excinfo.value.error == "date_range_too_large"


# ===========================================================================
# [15] TUSHARE LICENSING/ATTRIBUTION (cross-task audit)
# ===========================================================================
def test_item15_licensing_attribution_cross_task_audit(client: _FixtureClient) -> None:
    # Attribution holds on every fixture set, and no credential is committed.
    live_dirs = (_MD_DIR, _CA_DIR, _FU_DIR, _LI_DIR)
    for dirpath in live_dirs:
        provenance = _manifest(dirpath)["_provenance"]
        assert provenance["live_recorded"] is True, dirpath
        assert "proxy" in json.dumps(provenance).lower(), dirpath
    client_provenance = _manifest(_CL_DIR)["_provenance"]
    assert client_provenance["live_recorded"] is False
    assert "TUSHARE_PROXY_TOKEN" in client_provenance["reason"]
    identifiers_readme = (_ID_DIR / "README.md").read_text(encoding="utf-8")
    assert "Tushare" in identifiers_readme
    assert "constructed" in identifiers_readme

    # Every fixture file is JSON-attributed to the Tushare proxy contract and
    # none contains an obvious committed credential value.
    token_like = re.compile(r"(?i)(api[_-]?key|token)\s*[:=]\s*[A-Za-z0-9]{16,}")
    files = list(_FIXTURE_ROOT.rglob("*.json"))
    assert files
    for path in files:
        text = path.read_text(encoding="utf-8", errors="ignore")
        assert not token_like.search(text), path

    # No bulk redistribution: the largest recorded specimen is a bounded,
    # filtered audit sweep, far below the full A-share universe.
    largest = max(
        (entry.get("n_items", 0) for entry in _recordings(_manifest(_LI_DIR)).values()),
        default=0,
    )
    assert largest < 1000

    # The assembled output carries the attribution on every emitted fact.
    facts = _source([_PINGAN], client).get_fundamentals(
        "2022-06-30", "2022-06-30", ["total_revenue"]
    )
    assert (facts[F.SOURCE_VENDOR_COL] == "Tushare").all()
    assert (facts[F.ACCESS_PATH_COL] == "proxy:pcd.mobcvb.cn").all()

    # Retrieval provenance is NOT universal: the Wave 1 sets are
    # contract-modeled, so this cross-task item is NOT CERTIFIED.
    assert client_provenance["live_recorded"] is False
    assert "hand-constructed" in identifiers_readme


# ===========================================================================
# [16] KNOWN-MISSING (BLANK-OUT) HANDLING
# ===========================================================================
def test_item16a_blank_out_real_specimen_assembled() -> None:
    source = _source(["002450.SZ"], _FixtureClient())
    for period in ("2016-12-31", "2017-12-31"):
        frame = source.get_fundamentals(period, period, ["total_revenue", "n_income"])
        assert len(frame) == 2, period
        assert set(frame[FIELD_COL]) == {"total_revenue", "n_income"}
        assert frame[F.IS_BLANK_OUT_COL].all(), period
        assert frame[VALUE_COL].isna().all(), period
        assert not (frame[VALUE_COL] == 0.0).any(), period
        assert (frame[KNOWLEDGE_DATE_COL] == pd.Timestamp("2021-02-28")).all(), period


def test_item16b_blank_out_not_silent_zero_absence_or_restatement(
    client: _FixtureClient,
) -> None:
    source = _source(["002450.SZ"], client)

    # (a) The row is NOT absent.
    blank = source.get_fundamentals("2017-12-31", "2017-12-31", ["total_revenue"])
    assert len(blank) == 1

    # (c) Routine unavailability is row ABSENCE in this adapter, so the
    # blank-out row (present, NaN, tagged) is machine-distinguishable from a
    # genuinely never-reported field (raw None -> no row at all). The real
    # 002450.SZ FY2015 `int_income` is None on every vintage.
    absent = source.get_fundamentals("2015-12-31", "2015-12-31", ["int_income"])
    assert absent.empty
    # Every emitted NaN value is tagged, so a value-only reader cannot
    # encounter an untagged ordinary NaN.
    emitted = source.get_fundamentals("2016-12-31", "2016-12-31", ["total_revenue"])
    nan_rows = emitted[emitted[VALUE_COL].isna()]
    assert not nan_rows.empty
    assert nan_rows[F.IS_BLANK_OUT_COL].all()

    # (d) Blank-out and restatement are independent: restatement is driven by
    # the raw ann_date != f_ann_date signal, not by blankness. A non-blank
    # in-place-reprocessed row (002069.SZ FY2017) is also is_restatement=True,
    # and a clean row is False.
    raw = client.fetch("income", ts_code="002450.SZ", period="20171231")
    _, rows = _payload_rows(raw)
    assert rows[0]["total_revenue"] == ""  # blanked
    assert rows[0]["ann_date"] != rows[0]["f_ann_date"]  # separate date signal
    assert bool(blank.iloc[0][IS_RESTATEMENT_COL]) is True

    other = _source(["002069.SZ"], _FixtureClient()).get_fundamentals(
        "2017-12-31", "2017-12-31", ["total_revenue"]
    )
    assert bool(other.iloc[0][F.IS_BLANK_OUT_COL]) is False
    assert bool(other.iloc[0][IS_RESTATEMENT_COL]) is True

    clean = _source([_PINGAN], _FixtureClient()).get_fundamentals(
        "2022-06-30", "2022-06-30", ["total_revenue"]
    )
    assert bool(clean.iloc[0][IS_RESTATEMENT_COL]) is False


# ===========================================================================
# Report synchronization (guard only; every claim has an executable check)
# ===========================================================================
_LINE_LABELS = {
    1: "SCHEMA CONFORMANCE =",
    2: "CUMULATIVE VS SINGLE-QUARTER RECONCILIATION =",
    3: "CHINA FUNDAMENTALS VINTAGE CAPABILITY =",
    4: "CHINA FUNDAMENTALS VINTAGE COVERAGE COMPLETENESS =",
    5: "KNOWLEDGE-DATE RULE =",
    6: "CH3 NI-EX-NONRECURRING =",
    7: "TOTAL MARKET CAP =",
    8: "CH4 TURNOVER FEASIBILITY =",
    9: "CHANGING ADJ_FACTOR RECONSTRUCTION =",
    10: "IDENTIFIER CONTINUITY =",
    11: "DELISTING CORROBORATION =",
    12: "PROXY DETERMINISM =",
    13: "PROXY SUFFICIENT FOR PHASE 4D-B POC =",
    14: "PROXY SUFFICIENT FOR PRODUCTION =",
    15: "TUSHARE LICENSING/ATTRIBUTION =",
    16: "KNOWN-MISSING (BLANK-OUT) HANDLING =",
}

_STATUS_TOKENS = (
    "PASS",
    "FAIL",
    "NOT CERTIFIED",
    "NOT RUN",
    "YES",
    "NO",
    "FIELD-LEVEL",
)

_FROZEN = (
    (3, r"CHINA FUNDAMENTALS VINTAGE CAPABILITY = `?([A-Z ]+?)`?\*\*", "PASS"),
    (
        4,
        r"CHINA FUNDAMENTALS VINTAGE COVERAGE COMPLETENESS = `?([A-Z ]+?)`?\*\*",
        "NOT CERTIFIED",
    ),
    (13, r"PROXY SUFFICIENT FOR PHASE 4D-B POC = `?([A-Z]+)`?\*\*", "YES"),
    (14, r"PROXY SUFFICIENT FOR PRODUCTION = `?([A-Z]+)`?\*\*", "NO"),
)

_REQUIRED_VERBATIM = (
    "CHINA FUNDAMENTALS VINTAGE CAPABILITY = PASS",
    "CHINA FUNDAMENTALS VINTAGE COVERAGE COMPLETENESS = NOT CERTIFIED",
    "CH3 NI-EX-NONRECURRING = FIELD-LEVEL, VINTAGE-JOIN-DEPENDENT, NOT A SINGLE CANONICAL FIELD",
    "FLOAT MARKET CAP = DIAGNOSTIC ONLY, NOT INDEPENDENTLY CERTIFIED",
    "PROXY SUFFICIENT FOR PHASE 4D-B POC = YES",
    "PROXY SUFFICIENT FOR PRODUCTION = NO",
    # The item-5 caveat sentence from plan.md policy 3.
    "this is an adapter-internal parsing policy for Tushare's specific field semantics as observed through this proxy",
)


def test_certification_report_synchronized_with_dispositions() -> None:
    assert _REPORT_PATH.is_file(), f"missing certification report: {_REPORT_PATH}"
    document = _REPORT_PATH.read_text(encoding="utf-8")
    lines = document.splitlines()

    # --- Presence: all 16 line-items must be present with a status token.
    for index, label in _LINE_LABELS.items():
        anchor = f"**[{index}]"
        matching = [line for line in lines if line.startswith(anchor)]
        assert matching, f"check [{index}] is absent from the report"
        assert any(label in line for line in matching), (
            f"check [{index}] is present but lacks its label {label!r}"
        )
        assert any(
            token in line for line in matching for token in _STATUS_TOKENS
        ), f"check [{index}] has no explicit disposition token"

    # Item 7 carries both required statuses.
    assert (
        "FLOAT MARKET CAP = DIAGNOSTIC ONLY, NOT INDEPENDENTLY CERTIFIED"
        in document
    )

    # --- Frozen values: items 3, 4, 13, 14 must be exactly the frozen value.
    for index, pattern, expected in _FROZEN:
        match = re.search(pattern, document)
        assert match is not None, f"frozen item [{index}] line not parseable"
        observed = match.group(1).strip()
        assert observed == expected, (
            f"FROZEN VALUE VIOLATION: item [{index}] is {observed!r}, "
            f"must remain {expected!r}"
        )

    # --- Required verbatim lines (whitespace-normalized so prose line
    # wrapping cannot hide a missing sentence).
    normalized = re.sub(r"\s+", " ", document)
    for required in _REQUIRED_VERBATIM:
        assert required in normalized, f"missing report line: {required!r}"
