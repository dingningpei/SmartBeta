"""Tests for the Phase 5A real Treasury risk-free provider (P5A-1).

Everything here runs against the committed, live-recorded FRED ``DGS3MO``
fixture under ``tests/fixtures/risk_free/treasury/``; the suite makes **zero
live network calls** (explicitly guarded by ``test_no_live_network_calls``).

The formula tests are deliberately non-tautological (resolves B2): the expected
``rf`` values are frozen decimal literals computed with plain Python arithmetic
by the implementer, **never** by calling
:class:`TreasuryBillRiskFreeProvider` or any helper implementing the same
formula. A required negative guard asserts production output is not the
``/252``-compounded value.
"""

from __future__ import annotations

import io
import json
import socket
import urllib.request
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from smart_beta.data.schema import DATE_COL, RISK_FREE_COL, RISK_FREE_SCHEMA, validate_panel
from smart_beta.research_inputs.risk_free import RiskFreeProvider
from smart_beta.research_inputs.risk_free_treasury import (
    MAX_STALENESS_BUSINESS_DAYS,
    RiskFreeStalenessError,
    TreasuryBillRiskFreeProvider,
)

_FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "risk_free" / "treasury"
_FIXTURE_JSON = _FIXTURE_DIR / "dgs3mo_2025-09-01_2026-09-16.json"

# Tolerances (stated once, used everywhere):
#   * formula comparisons against the frozen literals: 1e-12 relative / 1e-15
#     absolute -- float64 round-off only;
#   * the /252 negative guard: the two conventions differ by >1e-6 for every
#     specimen here, so any |difference| <= 1e-6 would mean the guard failed.
FORMULA_REL_TOL = 1e-12
FORMULA_ABS_TOL = 1e-15
NEGATIVE_GUARD_TOL = 1e-6


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _load_raw_frame() -> pd.DataFrame:
    """The raw FRED CSV, exactly as recorded (blank holiday values included).

    The repository's ``.gitignore`` excludes ``*.csv``, so the verbatim FRED
    export lives as the ``raw_csv`` string inside the JSON fixture body; this
    parses those exact bytes.
    """
    body = json.loads(_FIXTURE_JSON.read_text(encoding="utf-8"))
    return pd.read_csv(io.StringIO(body["raw_csv"]))


def _make_provider(
    source: dict[str, float] | pd.DataFrame,
    trading_dates: list[str],
) -> TreasuryBillRiskFreeProvider:
    if isinstance(source, pd.DataFrame):
        frame = source
    else:
        frame = pd.DataFrame(
            {
                "observation_date": pd.to_datetime(list(source)),
                "DGS3MO": list(source.values()),
            }
        )
    return TreasuryBillRiskFreeProvider(frame, trading_dates=trading_dates)


def _fixture_provider(trading_dates: list[str]) -> TreasuryBillRiskFreeProvider:
    return TreasuryBillRiskFreeProvider(
        _load_raw_frame(), trading_dates=trading_dates
    )


# ---------------------------------------------------------------------------
# Contract / schema
# ---------------------------------------------------------------------------
def test_provider_satisfies_risk_free_contract():
    provider = _fixture_provider(["2025-09-02", "2025-09-03"])
    assert isinstance(provider, RiskFreeProvider)


def test_output_validates_against_risk_free_schema():
    dates = ["2025-09-02", "2025-09-03", "2025-09-04", "2025-09-05", "2025-09-08"]
    provider = _fixture_provider(dates)
    frame = provider.get_risk_free(dates[0], dates[-1])

    validate_panel(frame, RISK_FREE_SCHEMA, name="treasury risk-free")
    assert list(frame.columns) == [DATE_COL, RISK_FREE_COL]
    assert len(frame) == len(dates)
    assert pd.api.types.is_datetime64_any_dtype(frame[DATE_COL])
    assert pd.api.types.is_float_dtype(frame[RISK_FREE_COL])


# ---------------------------------------------------------------------------
# Frozen formula specimens (non-tautological: literals, plain arithmetic)
# ---------------------------------------------------------------------------
# Specimen A: 5.00% annualized, delta_calendar_days = 1  -> 0.05 * 1 / 365
SPECIMEN_A_EXPECTED = 0.05 * 1 / 365  # 0.0001369863013698630...
# Specimen B: 5.00% annualized, delta_calendar_days = 3  -> 0.05 * 3 / 365
SPECIMEN_B_EXPECTED = 0.05 * 3 / 365  # 0.0004109589041095890...


def test_specimen_a_simple_interest_over_365():
    provider = _make_provider({"2024-01-03": 5.00}, ["2024-01-02", "2024-01-03"])
    frame = provider.get_risk_free("2024-01-02", "2024-01-03")

    assert pd.isna(frame[RISK_FREE_COL].iloc[0])  # first-observation rule
    actual = float(frame[RISK_FREE_COL].iloc[1])
    assert actual == pytest.approx(
        SPECIMEN_A_EXPECTED, rel=FORMULA_REL_TOL, abs=FORMULA_ABS_TOL
    )


def test_specimen_b_simple_interest_over_365():
    provider = _make_provider({"2024-01-08": 5.00}, ["2024-01-05", "2024-01-08"])
    frame = provider.get_risk_free("2024-01-05", "2024-01-08")

    actual = float(frame[RISK_FREE_COL].iloc[1])
    assert actual == pytest.approx(
        SPECIMEN_B_EXPECTED, rel=FORMULA_REL_TOL, abs=FORMULA_ABS_TOL
    )


@pytest.mark.parametrize(
    "source_date, equity_dates, expected",
    [
        ("2024-01-03", ["2024-01-02", "2024-01-03"], SPECIMEN_A_EXPECTED),
        ("2024-01-08", ["2024-01-05", "2024-01-08"], SPECIMEN_B_EXPECTED),
    ],
)
def test_no_slash_252_compounding_negative_guard(source_date, equity_dates, expected):
    """Fail loudly if /252 compounding is ever substituted for /365 simple
    interest. The frozen literal and the compounded value differ by far more
    than ``NEGATIVE_GUARD_TOL`` for both specimens."""
    delta = int((pd.Timestamp(equity_dates[-1]) - pd.Timestamp(equity_dates[0])).days)
    compounded_252 = (1.0 + 0.05) ** (delta / 252.0) - 1.0

    provider = _make_provider({source_date: 5.00}, equity_dates)
    actual = float(provider.get_risk_free(equity_dates[0], equity_dates[-1])[RISK_FREE_COL].iloc[1])

    assert actual == pytest.approx(expected, rel=FORMULA_REL_TOL, abs=FORMULA_ABS_TOL)
    assert abs(actual - compounded_252) > NEGATIVE_GUARD_TOL
    # And the guard's own anchor: /252 is genuinely a different number.
    assert abs(expected - compounded_252) > NEGATIVE_GUARD_TOL


# ---------------------------------------------------------------------------
# Real fixture specimens (hand-derived literals, fixture-replayed production)
# ---------------------------------------------------------------------------
# All raw yields / source dates / deltas / staleness below were read out of the
# committed FRED fixture and the expected rf hand-computed with an independent
# arithmetic script -- not by the class under test.
REAL_WEEKDAY_GAP = {
    "equity_dates": ["2025-09-02", "2025-09-03"],  # Tue -> Wed, delta 1
    "expected_rf": 0.00011452054794520548,  # 4.18 / 100 * 1 / 365
    "source_date": "2025-09-03",
    "raw_yield": 4.18,
    "delta_calendar_days": 1,
    "staleness": 0,
}
REAL_WEEKEND_GAP = {
    "equity_dates": ["2025-09-05", "2025-09-08"],  # Fri -> Mon, delta 3
    "expected_rf": 0.00033698630136986304,  # 4.10 / 100 * 3 / 365
    "source_date": "2025-09-08",
    "raw_yield": 4.10,
    "delta_calendar_days": 3,
    "staleness": 0,
}
REAL_STALE_SOURCE = {
    # 2025-10-13 is Columbus Day: a Treasury-market holiday (blank in FRED)
    # but an equity trading day. The provider must fall back to 2025-10-10 and
    # the frozen weekday convention reports 1 business day (not 0).
    "equity_dates": ["2025-10-10", "2025-10-13"],
    "expected_rf": 0.00033041095890410960,  # 4.02 / 100 * 3 / 365
    "source_date": "2025-10-10",
    "raw_yield": 4.02,
    "delta_calendar_days": 3,
    "staleness": 1,
}


@pytest.mark.parametrize(
    "specimen",
    [REAL_WEEKDAY_GAP, REAL_WEEKEND_GAP, REAL_STALE_SOURCE],
    ids=["weekday_gap", "weekend_gap", "stale_source"],
)
def test_real_fixture_replayed_formula(specimen):
    dates = specimen["equity_dates"]
    provider = _fixture_provider(dates)
    frame = provider.get_risk_free(dates[0], dates[-1])

    assert pd.isna(frame[RISK_FREE_COL].iloc[0])
    actual = float(frame[RISK_FREE_COL].iloc[1])
    assert actual == pytest.approx(
        specimen["expected_rf"], rel=FORMULA_REL_TOL, abs=FORMULA_ABS_TOL
    )


# ---------------------------------------------------------------------------
# Staleness boundary (resolves B3)
# ---------------------------------------------------------------------------
def test_staleness_exactly_three_business_days_succeeds():
    # Source Fri 2024-01-05; equity Wed 2024-01-10 is exactly 3 business days
    # stale (Mon 8th, Tue 9th, Wed 10th).
    provider = _make_provider({"2024-01-05": 5.00}, ["2024-01-05", "2024-01-10"])
    assert provider.staleness_business_days("2024-01-05", "2024-01-10") == 3

    frame = provider.get_risk_free("2024-01-05", "2024-01-10")
    # delta_calendar_days = 5 (Fri -> Wed).
    assert float(frame[RISK_FREE_COL].iloc[1]) == pytest.approx(
        0.05 * 5 / 365, rel=FORMULA_REL_TOL, abs=FORMULA_ABS_TOL
    )


def test_staleness_four_business_days_raises():
    # Source Fri 2024-01-05; equity Thu 2024-01-11 is 4 business days stale.
    provider = _make_provider({"2024-01-05": 5.00}, ["2024-01-05", "2024-01-11"])
    assert provider.staleness_business_days("2024-01-05", "2024-01-11") == 4

    with pytest.raises(RiskFreeStalenessError):
        provider.get_risk_free("2024-01-05", "2024-01-11")


def test_no_observation_at_or_before_raises_same_named_exception():
    # The second queried date precedes every published DGS3MO observation:
    # an unbounded gap, trivially beyond 3 business days.
    provider = _make_provider({"2024-06-03": 5.00}, ["2024-01-02", "2024-01-03"])
    with pytest.raises(RiskFreeStalenessError):
        provider.get_risk_free("2024-01-02", "2024-01-03")


def test_first_row_unpriceable_does_not_raise_staleness_check():
    """Ordering (net effect): the first row is NaN before staleness is ever
    evaluated, even though that row has no observation at or before it."""
    provider = _make_provider({"2024-01-03": 5.00}, ["2024-01-02", "2024-01-04", "2024-01-05"])
    frame = provider.get_risk_free("2024-01-02", "2024-01-05")

    assert pd.isna(frame[RISK_FREE_COL].iloc[0])
    assert frame[RISK_FREE_COL].iloc[1:].notna().all()


def test_first_row_never_touches_lookup_or_staleness(monkeypatch):
    """Ordering (direct): spy on the lookup and staleness methods and assert
    neither is invoked for the first row of a call, whose equity date would
    otherwise fail the lookup outright."""
    provider = _make_provider(
        {"2024-01-03": 5.00},
        ["2024-01-02", "2024-01-04", "2024-01-05"],
    )

    lookup_calls: list[pd.Timestamp] = []
    staleness_calls: list[tuple[pd.Timestamp, pd.Timestamp]] = []
    original_lookup = TreasuryBillRiskFreeProvider.latest_published_observation
    original_staleness = TreasuryBillRiskFreeProvider.staleness_business_days

    def spy_lookup(self, equity_date):
        lookup_calls.append(pd.Timestamp(equity_date).normalize())
        return original_lookup(self, equity_date)

    def spy_staleness(source_date, equity_date):
        staleness_calls.append(
            (
                pd.Timestamp(source_date).normalize(),
                pd.Timestamp(equity_date).normalize(),
            )
        )
        return original_staleness(source_date, equity_date)

    monkeypatch.setattr(
        TreasuryBillRiskFreeProvider, "latest_published_observation", spy_lookup
    )
    monkeypatch.setattr(
        TreasuryBillRiskFreeProvider,
        "staleness_business_days",
        staticmethod(spy_staleness),
    )

    frame = provider.get_risk_free("2024-01-02", "2024-01-05")
    first = pd.Timestamp("2024-01-02")

    assert pd.isna(frame[RISK_FREE_COL].iloc[0])
    assert first not in lookup_calls
    assert all(equity != first for _, equity in staleness_calls)
    # Every later row *was* evaluated once.
    assert lookup_calls == [pd.Timestamp("2024-01-04"), pd.Timestamp("2024-01-05")]
    assert [equity for _, equity in staleness_calls] == [
        pd.Timestamp("2024-01-04"),
        pd.Timestamp("2024-01-05"),
    ]


# ---------------------------------------------------------------------------
# No future leakage (direct assertion on the lookup logic)
# ---------------------------------------------------------------------------
def test_latest_published_observation_never_reads_future():
    provider = _make_provider(
        {"2024-01-01": 4.00, "2024-02-01": 4.50, "2024-03-01": 5.00},
        ["2024-01-02"],
    )

    # Strictly the latest observation at or before the query date.
    assert provider.latest_published_observation("2024-01-15") == (
        pd.Timestamp("2024-01-01"),
        4.00,
    )
    # Exact-date observation is used, never the following one.
    assert provider.latest_published_observation("2024-02-01") == (
        pd.Timestamp("2024-02-01"),
        4.50,
    )
    # Before the first observation: nothing at all.
    assert provider.latest_published_observation("2023-12-31") is None

    # The returned source date is structurally <= equity date for every query.
    for query in pd.date_range("2024-01-01", "2024-03-31", freq="D"):
        found = provider.latest_published_observation(query)
        if found is not None:
            assert found[0] <= query


def test_future_observation_is_ignored_by_get_risk_free():
    # A wildly different future observation must not leak backward.
    provider = _make_provider(
        {"2024-01-03": 5.00, "2024-01-04": 99.00},
        ["2024-01-02", "2024-01-03"],
    )
    frame = provider.get_risk_free("2024-01-02", "2024-01-03")
    assert float(frame[RISK_FREE_COL].iloc[1]) == pytest.approx(
        0.05 * 1 / 365, rel=FORMULA_REL_TOL, abs=FORMULA_ABS_TOL
    )


# ---------------------------------------------------------------------------
# First-observation NaN
# ---------------------------------------------------------------------------
def test_first_date_in_requested_range_is_nan():
    provider = _fixture_provider(["2025-09-02", "2025-09-03", "2025-09-04"])
    frame = provider.get_risk_free("2025-09-02", "2025-09-04")

    assert pd.isna(frame[RISK_FREE_COL].iloc[0])
    assert frame[RISK_FREE_COL].iloc[1:].notna().all()

    # A sub-call that starts mid-sequence still emits NaN for its own first row.
    interior = provider.get_risk_free("2025-09-03", "2025-09-04")
    assert pd.isna(interior[RISK_FREE_COL].iloc[0])
    assert pd.notna(interior[RISK_FREE_COL].iloc[1])


# ---------------------------------------------------------------------------
# Diagnostic provenance (real fixture)
# ---------------------------------------------------------------------------
def test_real_fixture_diagnostic_provenance():
    dates = REAL_STALE_SOURCE["equity_dates"]
    provider = _fixture_provider(dates)
    provider.get_risk_free(dates[0], dates[-1])

    diagnostics = provider.last_diagnostics
    assert list(diagnostics.columns) == [
        DATE_COL,
        "preceding_date",
        "delta_calendar_days",
        "source_date",
        "raw_yield",
        "staleness_business_days",
        RISK_FREE_COL,
    ]

    first = diagnostics.iloc[0]
    assert first[DATE_COL] == pd.Timestamp(dates[0])
    assert pd.isna(first["preceding_date"])
    assert pd.isna(first["delta_calendar_days"])
    assert pd.isna(first["source_date"])
    assert pd.isna(first["staleness_business_days"])
    assert pd.isna(first[RISK_FREE_COL])

    second = diagnostics.iloc[1]
    assert second[DATE_COL] == pd.Timestamp(dates[1])
    assert second["preceding_date"] == pd.Timestamp(dates[0])
    assert second["delta_calendar_days"] == REAL_STALE_SOURCE["delta_calendar_days"]
    assert second["source_date"] == pd.Timestamp(REAL_STALE_SOURCE["source_date"])
    assert second["raw_yield"] == pytest.approx(REAL_STALE_SOURCE["raw_yield"])
    assert (
        second["staleness_business_days"]
        == REAL_STALE_SOURCE["staleness"]
    )
    assert second[RISK_FREE_COL] == pytest.approx(
        REAL_STALE_SOURCE["expected_rf"], rel=FORMULA_REL_TOL, abs=FORMULA_ABS_TOL
    )


def test_last_diagnostics_is_independent_copy():
    dates = REAL_WEEKDAY_GAP["equity_dates"]
    provider = _fixture_provider(dates)
    provider.get_risk_free(dates[0], dates[-1])

    snapshot = provider.last_diagnostics
    snapshot.loc[snapshot.index[0], "raw_yield"] = -1.0
    assert provider.last_diagnostics.iloc[0]["raw_yield"] != -1.0


# ---------------------------------------------------------------------------
# Zero live network calls
# ---------------------------------------------------------------------------
def test_no_live_network_calls(monkeypatch):
    """The fixture-replayed suite must not touch the network. This guard makes
    any future accidental live fetch fail loudly inside pytest."""

    def _blocked(*args, **kwargs):  # pragma: no cover - only fires on failure
        raise AssertionError("live network call attempted during pytest")

    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)
    monkeypatch.setattr(urllib.request, "urlopen", _blocked)

    dates = REAL_WEEKEND_GAP["equity_dates"]
    provider = _fixture_provider(dates)
    frame = provider.get_risk_free(dates[0], dates[-1])
    assert len(frame) == 2


# ---------------------------------------------------------------------------
# Frozen constants / input hygiene
# ---------------------------------------------------------------------------
def test_frozen_max_staleness_constant():
    assert MAX_STALENESS_BUSINESS_DAYS == 3


def test_holiday_blank_rows_are_not_observations():
    # 2025-10-13 (Columbus Day) is blank in the raw FRED fixture.
    provider = _fixture_provider(["2025-10-13"])
    raw = _load_raw_frame()
    blank = raw.loc[raw["observation_date"] == "2025-10-13", "DGS3MO"]
    assert len(blank) == 1 and pd.isna(blank.iloc[0])

    found = provider.latest_published_observation("2025-10-13")
    assert found is not None
    assert found[0] == pd.Timestamp("2025-10-10")


def test_fixture_provenance_manifest_matches_raw_csv():
    import hashlib

    manifest = json.loads((_FIXTURE_DIR / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["_provenance"]["live_recorded"] is True
    assert manifest["_provenance"]["series_id"] == "DGS3MO"

    body = json.loads(_FIXTURE_JSON.read_text(encoding="utf-8"))
    raw_bytes = body["raw_csv"].encode("utf-8")
    recorded = manifest["recordings"][_FIXTURE_JSON.name]
    assert recorded["status_code"] == 200
    assert recorded["raw_csv_sha256"] == hashlib.sha256(raw_bytes).hexdigest()
    assert len(body["observations"]) == recorded["n_date_rows"]


def test_empty_requested_window_returns_empty_schema_frame():
    provider = _fixture_provider(["2025-09-02", "2025-09-03"])
    frame = provider.get_risk_free("2024-01-01", "2024-01-05")
    assert frame.empty
    validate_panel(frame, RISK_FREE_SCHEMA, name="empty treasury risk-free")


def test_provider_does_not_mutate_supplied_inputs():
    raw = _load_raw_frame()
    raw_copy = raw.copy(deep=True)
    trading_dates = ["2025-09-02", "2025-09-03"]

    provider = TreasuryBillRiskFreeProvider(raw, trading_dates=trading_dates)
    provider.get_risk_free(trading_dates[0], trading_dates[-1])

    pd.testing.assert_frame_equal(raw, raw_copy)
    assert trading_dates == ["2025-09-02", "2025-09-03"]
