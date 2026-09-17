"""Tests for Tiingo as-reported fundamentals mapping (Phase 4B, P4B-6).

Every test here is offline. Mapping is a pure function of recorded JSON;
the RGEN 400 specimen is replayed through :func:`replay_transport`. An
autouse fixture replaces ``urllib.request.urlopen`` with a tripwire so an
accidental live call fails loudly.
"""

from __future__ import annotations

import inspect
import json
import urllib.request
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
    validate_panel,
)
from smart_beta.vendors.tiingo.client import (
    TiingoAPIError,
    TiingoClient,
    replay_transport,
)
from smart_beta.vendors.tiingo import fundamentals as fundamentals_mod
from smart_beta.vendors.tiingo.fundamentals import (
    DERIVED_FIELDS,
    FISCAL_QUARTER_FIELD,
    FISCAL_YEAR_FIELD,
    FiscalPeriodReconciliationError,
    RAW_LINE_ITEM_FIELDS,
    map_asreported_to_fundamentals,
    reconcile_report_period_end,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "tiingo" / "fundamentals"

_AAPL_Q3_PERIOD_END = pd.Timestamp("2026-06-27")
_AAPL_Q3_KNOWLEDGE_DATE = pd.Timestamp("2026-07-31")
_CALENDAR_QUARTER_END = pd.Timestamp("2026-06-30")
_STOCK_ID = "AAPL"
_Q3_FIELDS = ("revenue", "netinc", "totalAssets")

_REQUIRED_DOC_SENTENCE = (
    "False != verified non-restated; restatement status remains NOT CERTIFIED"
)

_PROVENANCE_COLS = ("_source_vendor", "_source_endpoint", "_ingested_at")


def _load(filename: str) -> object:
    return json.loads((FIXTURE_DIR / filename).read_text(encoding="utf-8"))


def _asreported() -> list[dict]:
    body = _load("aapl_fundamentals_asreported.json")
    assert isinstance(body, list)
    return body


def _normalized() -> list[dict]:
    body = _load("aapl_fundamentals_normalized.json")
    assert isinstance(body, list)
    return body


def _q3(statements: list[dict]) -> dict:
    matches = [
        s
        for s in statements
        if s[FISCAL_YEAR_FIELD] == 2026 and s[FISCAL_QUARTER_FIELD] == 3
    ]
    assert len(matches) == 1
    return matches[0]


@pytest.fixture(autouse=True)
def _forbid_live_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*args: object, **kwargs: object) -> object:
        raise AssertionError(
            "Test attempted a live network call; inject a transport instead."
        )

    monkeypatch.setattr(urllib.request, "urlopen", _boom)


# ---------------------------------------------------------------------------
# Fixture sanity: the captured AAPL specimen really is the required one
# ---------------------------------------------------------------------------
def test_captured_aapl_q3_2026_dates_match_spec() -> None:
    as_reported = _q3(_asreported())
    normalized = _q3(_normalized())
    assert as_reported["date"] == "2026-07-31"
    assert normalized["date"] == "2026-06-27"
    assert as_reported[FISCAL_YEAR_FIELD] == 2026
    assert as_reported[FISCAL_QUARTER_FIELD] == 3
    assert normalized[FISCAL_YEAR_FIELD] == 2026
    assert normalized[FISCAL_QUARTER_FIELD] == 3
    # Fiscal identity is year/quarter, not fiscalYear/fiscalQuarter.
    assert "fiscalYear" not in as_reported
    assert "fiscalYear" not in normalized


def test_asreported_and_normalized_coverage_is_not_one_to_one() -> None:
    as_ids = {
        (s[FISCAL_YEAR_FIELD], s[FISCAL_QUARTER_FIELD]) for s in _asreported()
    }
    norm_ids = {
        (s[FISCAL_YEAR_FIELD], s[FISCAL_QUARTER_FIELD]) for s in _normalized()
    }
    assert as_ids != norm_ids
    assert (2026, 1) in as_ids
    assert (2026, 1) not in norm_ids


# ---------------------------------------------------------------------------
# 1. Valid case, exact values, non-calendar quarter
# ---------------------------------------------------------------------------
def test_aapl_fiscal_q3_2026_exact_non_calendar_period_end() -> None:
    frame = map_asreported_to_fundamentals(
        _STOCK_ID, [_q3(_asreported())], _normalized(), _Q3_FIELDS
    )
    assert not frame.empty
    assert (frame[REPORT_PERIOD_END_COL] == _AAPL_Q3_PERIOD_END).all()
    assert (frame[KNOWLEDGE_DATE_COL] == _AAPL_Q3_KNOWLEDGE_DATE).all()
    # A calendar-quarter implementation would emit 2026-06-30 and must
    # fail this assertion. Exact equality, not an approximate window.
    assert (frame[REPORT_PERIOD_END_COL] != _CALENDAR_QUARTER_END).all()
    assert _AAPL_Q3_PERIOD_END != _CALENDAR_QUARTER_END

    revenue = frame.loc[frame[FIELD_COL] == "revenue", VALUE_COL]
    netinc = frame.loc[frame[FIELD_COL] == "netinc", VALUE_COL]
    assets = frame.loc[frame[FIELD_COL] == "totalAssets", VALUE_COL]
    assert list(revenue) == [109417000000.0]
    assert list(netinc) == [29789000000.0]
    assert list(assets) == [383266000000.0]


def test_reconcile_aapl_q3_returns_normalized_date_not_calendar_end() -> None:
    period_end = reconcile_report_period_end(_q3(_asreported()), _normalized())
    assert period_end == _AAPL_Q3_PERIOD_END
    assert period_end != _CALENDAR_QUARTER_END
    assert period_end != pd.Timestamp(_q3(_asreported())["date"])


# ---------------------------------------------------------------------------
# 2. Missing period-end match -> explicit failure, whole-batch abort
# ---------------------------------------------------------------------------
def test_zero_matches_raises_on_reconcile() -> None:
    constructed = _load("constructed_normalized_q3_removed.json")
    assert isinstance(constructed, list)
    with pytest.raises(FiscalPeriodReconciliationError, match="got 0"):
        reconcile_report_period_end(_q3(_asreported()), constructed)


def test_zero_matches_aborts_whole_batch_with_no_partial_frame() -> None:
    constructed = _load("constructed_normalized_q3_removed.json")
    assert isinstance(constructed, list)
    # Q2 would match the remaining normalized entry; Q3 would not. A
    # silent-drop implementation would return Q2 rows. Fail closed instead.
    q2 = [
        s
        for s in _asreported()
        if s[FISCAL_YEAR_FIELD] == 2026 and s[FISCAL_QUARTER_FIELD] == 2
    ]
    q3 = [_q3(_asreported())]
    with pytest.raises(FiscalPeriodReconciliationError, match="got 0") as exc_info:
        map_asreported_to_fundamentals(
            _STOCK_ID, q2 + q3, constructed, _Q3_FIELDS
        )
    assert exc_info.value.args  # exception, not a DataFrame
    assert not isinstance(exc_info.value, pd.DataFrame)


def test_real_coverage_gap_q1_aborts_whole_asreported_batch() -> None:
    # Live captures: asReported has 2026 Q1, normalized does not.
    with pytest.raises(FiscalPeriodReconciliationError, match="got 0"):
        map_asreported_to_fundamentals(
            _STOCK_ID, _asreported(), _normalized(), _Q3_FIELDS
        )


# ---------------------------------------------------------------------------
# 3. Duplicate period-end matches -> explicit failure, whole-batch abort
# ---------------------------------------------------------------------------
def test_duplicate_matches_raises_on_reconcile() -> None:
    constructed = _load("constructed_normalized_q3_duplicated.json")
    assert isinstance(constructed, list)
    with pytest.raises(FiscalPeriodReconciliationError, match="got 2"):
        reconcile_report_period_end(_q3(_asreported()), constructed)


def test_duplicate_matches_aborts_whole_batch_with_no_partial_frame() -> None:
    constructed = _load("constructed_normalized_q3_duplicated.json")
    assert isinstance(constructed, list)
    q2 = [
        s
        for s in _asreported()
        if s[FISCAL_YEAR_FIELD] == 2026 and s[FISCAL_QUARTER_FIELD] == 2
    ]
    q3 = [_q3(_asreported())]
    with pytest.raises(FiscalPeriodReconciliationError, match="got 2"):
        map_asreported_to_fundamentals(
            _STOCK_ID, q2 + q3, constructed, _Q3_FIELDS
        )


def test_map_does_not_catch_reconciliation_errors() -> None:
    source = inspect.getsource(map_asreported_to_fundamentals)
    assert "except FiscalPeriodReconciliationError" not in source
    assert "except Exception" not in source


# ---------------------------------------------------------------------------
# 4. knowledge_date is never substituted for report_period_end
# ---------------------------------------------------------------------------
def test_knowledge_date_not_substituted_for_report_period_end() -> None:
    frame = map_asreported_to_fundamentals(
        _STOCK_ID, [_q3(_asreported())], _normalized(), _Q3_FIELDS
    )
    # The two dates are genuinely different on this specimen; a swap
    # cannot coincidentally look right.
    assert _AAPL_Q3_PERIOD_END != _AAPL_Q3_KNOWLEDGE_DATE
    assert (frame[REPORT_PERIOD_END_COL] == _AAPL_Q3_PERIOD_END).all()
    assert (frame[KNOWLEDGE_DATE_COL] == _AAPL_Q3_KNOWLEDGE_DATE).all()
    assert not (frame[REPORT_PERIOD_END_COL] == frame[KNOWLEDGE_DATE_COL]).any()
    assert (frame[REPORT_PERIOD_END_COL] != _AAPL_Q3_KNOWLEDGE_DATE).all()
    assert (frame[KNOWLEDGE_DATE_COL] != _AAPL_Q3_PERIOD_END).all()


# ---------------------------------------------------------------------------
# 5. Derived-field exclusion
# ---------------------------------------------------------------------------
def test_derived_piotroski_f_score_unreachable_even_when_requested() -> None:
    assert "piotroskiFScore" in DERIVED_FIELDS
    assert "piotroskiFScore" not in RAW_LINE_ITEM_FIELDS
    # The captured statement actually contains the derived field.
    overview = _q3(_asreported())["statementData"]["overview"]
    assert any(item["dataCode"] == "piotroskiFScore" for item in overview)

    frame = map_asreported_to_fundamentals(
        _STOCK_ID,
        [_q3(_asreported())],
        _normalized(),
        ["revenue", "piotroskiFScore", "profitMargin", "roe"],
    )
    emitted = set(frame[FIELD_COL].tolist())
    assert "revenue" in emitted
    assert "piotroskiFScore" not in emitted
    assert "profitMargin" not in emitted
    assert "roe" not in emitted
    assert emitted.isdisjoint(DERIVED_FIELDS)


def test_requesting_only_derived_fields_emits_no_rows() -> None:
    frame = map_asreported_to_fundamentals(
        _STOCK_ID,
        [_q3(_asreported())],
        _normalized(),
        ["piotroskiFScore", "profitMargin"],
    )
    assert frame.empty
    validate_panel(frame, FUNDAMENTALS_FACT_SCHEMA, name="tiingo_fundamentals")


# ---------------------------------------------------------------------------
# 6. is_restatement is always False; required docstring sentence
# ---------------------------------------------------------------------------
def test_is_restatement_always_false() -> None:
    frame = map_asreported_to_fundamentals(
        _STOCK_ID, [_q3(_asreported())], _normalized(), _Q3_FIELDS
    )
    assert frame[IS_RESTATEMENT_COL].dtype == bool or pd.api.types.is_bool_dtype(
        frame[IS_RESTATEMENT_COL]
    )
    assert (frame[IS_RESTATEMENT_COL] == False).all()  # noqa: E712
    assert not frame[IS_RESTATEMENT_COL].any()


def test_module_docstring_contains_required_restatement_sentence() -> None:
    assert fundamentals_mod.__doc__ is not None
    assert _REQUIRED_DOC_SENTENCE in fundamentals_mod.__doc__
    assert map_asreported_to_fundamentals.__doc__ is not None
    assert _REQUIRED_DOC_SENTENCE in map_asreported_to_fundamentals.__doc__


# ---------------------------------------------------------------------------
# 7. Schema conformance
# ---------------------------------------------------------------------------
def test_output_conforms_to_fundamentals_fact_schema() -> None:
    frame = map_asreported_to_fundamentals(
        _STOCK_ID, [_q3(_asreported())], _normalized(), _Q3_FIELDS
    )
    validate_panel(frame, FUNDAMENTALS_FACT_SCHEMA, name="tiingo_fundamentals")
    assert (frame[STOCK_COL] == _STOCK_ID).all()


def test_empty_fields_still_schema_conformant_after_successful_reconcile() -> None:
    frame = map_asreported_to_fundamentals(
        _STOCK_ID, [_q3(_asreported())], _normalized(), []
    )
    assert frame.empty
    validate_panel(frame, FUNDAMENTALS_FACT_SCHEMA, name="tiingo_fundamentals")


# ---------------------------------------------------------------------------
# 8. Provenance columns present
# ---------------------------------------------------------------------------
def test_provenance_columns_present() -> None:
    frame = map_asreported_to_fundamentals(
        _STOCK_ID, [_q3(_asreported())], _normalized(), _Q3_FIELDS
    )
    for col in _PROVENANCE_COLS:
        assert col in frame.columns
    assert (frame["_source_vendor"] == "tiingo").all()
    assert (frame["_source_endpoint"] == "get_fundamentals_asreported").all()
    assert pd.api.types.is_datetime64_any_dtype(frame["_ingested_at"])


# ---------------------------------------------------------------------------
# RGEN 400: caller sees TiingoAPIError; this module does not paper over it
# ---------------------------------------------------------------------------
def test_rgen_asreported_400_surfaces_as_tiingo_api_error() -> None:
    body = _load("rgen_fundamentals_asreported_error.json")
    transport = replay_transport(
        {"/tiingo/fundamentals/RGEN/statements": (400, body)}
    )
    client = TiingoClient(transport=transport)
    with pytest.raises(TiingoAPIError) as exc_info:
        client.get_fundamentals_asreported("RGEN")
    assert exc_info.value.status_code == 400
    assert exc_info.value.body == body


def test_mapping_module_does_not_catch_or_wrap_tiingo_api_error() -> None:
    source = inspect.getsource(fundamentals_mod)
    # No client fetch lives in this module, so there is nothing that
    # could catch a 400 and return an empty/partial frame.
    assert "TiingoAPIError" not in source
    assert "TiingoClient" not in source
