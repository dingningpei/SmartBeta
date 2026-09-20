"""Tests for the Phase 5B PBOC China risk-free provider (P5B-1).

Everything here runs against the committed, live-recorded PBOC fixture under
``tests/fixtures/phase5b/china_rf/``; the suite makes **zero live network
calls** (explicitly guarded by ``test_no_live_network_calls``).

The formula tests are deliberately non-tautological: the expected ``rf``
values are frozen decimal literals computed with plain Python arithmetic by the
implementer, **never** by calling
:class:`ChinaPBOCDepositRiskFreeProvider` or any helper implementing the same
formula. Required negative guards assert the production output is neither the
compounded nor the Actual/360 value.
"""

from __future__ import annotations

import hashlib
import json
import socket
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from smart_beta.data.schema import (
    DATE_COL,
    RISK_FREE_COL,
    RISK_FREE_SCHEMA,
    validate_panel,
)
from smart_beta.pit.calendar import TradingCalendar
from smart_beta.research_inputs.risk_free import RiskFreeProvider
from smart_beta.research_inputs.risk_free_china import (
    DEFAULT_DAY_COUNT_BASIS,
    ChinaPBOCDepositRiskFreeProvider,
)

_FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "phase5b" / "china_rf"
_FIXTURE_JSON = _FIXTURE_DIR / "pboc_one_year_deposit_rate_history.json"

# Tolerances (stated once, used everywhere):
#   * formula comparisons against the frozen literals: 1e-12 relative / 1e-15
#     absolute -- float64 round-off only;
#   * the convention negative guards: the simple-pro-rating value differs from
#     the compounded and Actual/360 values by far more than this for every
#     specimen here, so any |difference| <= 1e-6 would mean the guard failed.
FORMULA_REL_TOL = 1e-12
FORMULA_ABS_TOL = 1e-15
NEGATIVE_GUARD_TOL = 1e-6


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _load_body() -> dict:
    return json.loads(_FIXTURE_JSON.read_text(encoding="utf-8"))


def _load_observations() -> pd.DataFrame:
    return pd.DataFrame(_load_body()["observations"])


def _fixture_provider(
    trading_dates: list[str],
    *,
    day_count_basis: int = DEFAULT_DAY_COUNT_BASIS,
) -> ChinaPBOCDepositRiskFreeProvider:
    return ChinaPBOCDepositRiskFreeProvider(
        _load_observations(),
        trading_dates=trading_dates,
        day_count_basis=day_count_basis,
    )


def _make_provider(
    records: list[dict[str, object]],
    trading_dates: list[str],
    *,
    day_count_basis: int = DEFAULT_DAY_COUNT_BASIS,
) -> ChinaPBOCDepositRiskFreeProvider:
    return ChinaPBOCDepositRiskFreeProvider(
        pd.DataFrame(records),
        trading_dates=trading_dates,
        day_count_basis=day_count_basis,
    )


# ---------------------------------------------------------------------------
# Contract / schema
# ---------------------------------------------------------------------------
def test_provider_satisfies_risk_free_contract():
    provider = _fixture_provider(["2015-10-22", "2015-10-23"])
    assert isinstance(provider, RiskFreeProvider)


def test_output_validates_against_risk_free_schema():
    dates = ["2015-10-22", "2015-10-23", "2015-10-26"]
    provider = _fixture_provider(dates)
    frame = provider.get_risk_free(dates[0], dates[-1])

    validate_panel(frame, RISK_FREE_SCHEMA, name="china risk-free")
    assert list(frame.columns) == [DATE_COL, RISK_FREE_COL]
    assert len(frame) == len(dates)
    assert pd.api.types.is_datetime64_any_dtype(frame[DATE_COL])
    assert pd.api.types.is_float_dtype(frame[RISK_FREE_COL])


# ---------------------------------------------------------------------------
# Frozen formula specimens (non-tautological: literals, plain arithmetic)
# ---------------------------------------------------------------------------
# Specimen A: 1.50% annualized, delta_calendar_days = 1 -> 0.015 * 1 / 365
SPECIMEN_A_EXPECTED = 0.015 * 1 / 365  # 4.1095890410958904e-05
# Specimen B: 1.50% annualized, delta_calendar_days = 3 -> 0.015 * 3 / 365
SPECIMEN_B_EXPECTED = 0.015 * 3 / 365  # 0.00012328767123287672

_SPECIMEN_A_RECORDS = [
    {"announcement_date": "2020-01-01", "effective_date": "2020-01-01", "rate": 1.50}
]


def test_specimen_a_simple_interest_over_365():
    provider = _make_provider(
        _SPECIMEN_A_RECORDS, ["2020-01-02", "2020-01-03"]
    )
    frame = provider.get_risk_free("2020-01-02", "2020-01-03")

    assert pd.isna(frame[RISK_FREE_COL].iloc[0])  # first-observation rule
    actual = float(frame[RISK_FREE_COL].iloc[1])
    assert actual == pytest.approx(
        SPECIMEN_A_EXPECTED, rel=FORMULA_REL_TOL, abs=FORMULA_ABS_TOL
    )


def test_specimen_b_simple_interest_over_365():
    provider = _make_provider(
        _SPECIMEN_A_RECORDS, ["2020-01-03", "2020-01-06"]
    )
    frame = provider.get_risk_free("2020-01-03", "2020-01-06")

    actual = float(frame[RISK_FREE_COL].iloc[1])
    assert actual == pytest.approx(
        SPECIMEN_B_EXPECTED, rel=FORMULA_REL_TOL, abs=FORMULA_ABS_TOL
    )


def test_no_compounding_and_no_actual_360_negative_guards():
    """Fail loudly if compounding or Actual/360 is ever substituted for the
    frozen simple pro-rating / Actual/365 default.

    Specimen: 5.00% annualized over a 180-calendar-day gap (2019-01-02 ->
    2019-07-01). The simple, compounded, and /360 values differ by far more
    than ``NEGATIVE_GUARD_TOL``."""
    records = [
        {
            "announcement_date": "2019-01-01",
            "effective_date": "2019-01-01",
            "rate": 5.00,
        }
    ]
    provider = _make_provider(records, ["2019-01-02", "2019-07-01"])
    dates = ["2019-01-02", "2019-07-01"]
    delta = int(
        (pd.Timestamp(dates[-1]) - pd.Timestamp(dates[0])).days
    )
    assert delta == 180

    expected_simple = 0.05 * 180 / 365
    compounded = (1.0 + 0.05) ** (180 / 365.0) - 1.0
    actual_360 = 0.05 * 180 / 360

    actual = float(
        provider.get_risk_free(dates[0], dates[-1])[RISK_FREE_COL].iloc[1]
    )

    assert actual == pytest.approx(
        expected_simple, rel=FORMULA_REL_TOL, abs=FORMULA_ABS_TOL
    )
    assert abs(actual - compounded) > NEGATIVE_GUARD_TOL
    assert abs(actual - actual_360) > NEGATIVE_GUARD_TOL
    # The guards' own anchors: the alternatives are genuinely different.
    assert abs(expected_simple - compounded) > NEGATIVE_GUARD_TOL
    assert abs(expected_simple - actual_360) > NEGATIVE_GUARD_TOL


# ---------------------------------------------------------------------------
# Piecewise-constant step lookup
# ---------------------------------------------------------------------------
def test_step_lookup_uses_most_recent_effective_date():
    records = [
        {"announcement_date": "2020-01-01", "effective_date": "2020-01-01", "rate": 1.0},
        {"announcement_date": "2020-06-01", "effective_date": "2020-06-01", "rate": 2.0},
        {"announcement_date": "2020-12-01", "effective_date": "2020-12-01", "rate": 3.0},
    ]
    provider = _make_provider(records, ["2020-01-02"])

    assert provider.latest_announcement_as_of("2019-12-31") is None
    assert provider.latest_announcement_as_of("2020-01-01")[2] == 1.0
    assert provider.latest_announcement_as_of("2020-05-31")[2] == 1.0
    assert provider.latest_announcement_as_of("2020-06-01")[2] == 2.0
    assert provider.latest_announcement_as_of("2020-11-30")[2] == 2.0
    assert provider.latest_announcement_as_of("2020-12-01")[2] == 3.0
    assert provider.latest_announcement_as_of("2021-01-01")[2] == 3.0


def test_rate_changes_on_effective_date_exactly():
    records = [
        {"announcement_date": "2020-01-01", "effective_date": "2020-01-01", "rate": 3.00},
        {"announcement_date": "2020-03-01", "effective_date": "2020-03-01", "rate": 1.00},
    ]
    # 2020-03-01 is a Sunday; 2020-02-28 Fri -> 2020-03-02 Mon spans it.
    provider = _make_provider(records, ["2020-02-28", "2020-03-02"])
    frame = provider.get_risk_free("2020-02-28", "2020-03-02")

    # Row 0 is the first-observation NaN; row 1 uses the rate effective 03-01.
    assert float(frame[RISK_FREE_COL].iloc[1]) == pytest.approx(
        0.01 * 3 / 365, rel=FORMULA_REL_TOL, abs=FORMULA_ABS_TOL
    )


# ---------------------------------------------------------------------------
# Announcement / effective-date semantics
# ---------------------------------------------------------------------------
def test_effective_date_before_announcement_date_is_rejected():
    records = [
        {
            "announcement_date": "2020-06-01",
            "effective_date": "2020-05-01",
            "rate": 2.00,
        }
    ]
    with pytest.raises(ValueError, match="well-formed"):
        _make_provider(records, ["2020-06-02"])


def test_announced_but_not_yet_effective_change_does_not_apply_early():
    """A rate announced before it takes effect must not be applied before its
    effective date; the prior rate remains in force through the gap."""
    records = [
        {"announcement_date": "2015-08-26", "effective_date": "2015-08-26", "rate": 1.75},
        {"announcement_date": "2015-10-23", "effective_date": "2015-10-24", "rate": 1.50},
    ]
    provider = _make_provider(records, ["2015-10-22", "2015-10-23", "2015-10-26"])

    # The 2015-10-24 record was announced 2015-10-23, but is not effective yet.
    on_announcement = provider.latest_announcement_as_of("2015-10-23")
    assert on_announcement is not None
    assert on_announcement[0] == pd.Timestamp("2015-08-26")
    assert on_announcement[2] == 1.75

    on_effective = provider.latest_announcement_as_of("2015-10-24")
    assert on_effective is not None
    assert on_effective[0] == pd.Timestamp("2015-10-24")
    assert on_effective[1] == pd.Timestamp("2015-10-23")
    assert on_effective[2] == 1.50

    frame = provider.get_risk_free("2015-10-22", "2015-10-26")
    assert float(frame[RISK_FREE_COL].iloc[1]) == pytest.approx(
        0.0175 * 1 / 365, rel=FORMULA_REL_TOL, abs=FORMULA_ABS_TOL
    )
    assert float(frame[RISK_FREE_COL].iloc[2]) == pytest.approx(
        0.0150 * 3 / 365, rel=FORMULA_REL_TOL, abs=FORMULA_ABS_TOL
    )


def test_announcement_pit_clause_is_enforced_against_corrupted_source():
    """The explicit ``announcement_date <= t`` contract clause is real, not
    decorative: if the in-memory source is mutated to violate it after
    construction, the lookup raises rather than leaking."""
    with pytest.raises(ValueError, match="well-formed"):
        _make_provider(
            [
                {
                    "announcement_date": "2025-01-01",
                    "effective_date": "2020-01-01",
                    "rate": 1.0,
                }
            ],
            ["2020-01-02"],
        )

    provider = _make_provider(
        [
            {
                "announcement_date": "2020-01-01",
                "effective_date": "2020-01-01",
                "rate": 1.0,
            }
        ],
        ["2020-01-02"],
    )
    # Corrupt the already-constructed source so the selected record's
    # announcement date lands after the query date.
    provider._announcement_dates = pd.DatetimeIndex(["2020-06-01"])
    with pytest.raises(ValueError, match="internal inconsistency"):
        provider.latest_announcement_as_of("2020-03-01")


# ---------------------------------------------------------------------------
# Real fixture specimens (hand-derived literals, fixture-replayed production)
# ---------------------------------------------------------------------------
# All raw rates / effective dates / deltas below were read out of the committed
# PBOC fixture and the expected rf hand-computed with an independent arithmetic
# script -- not by the class under test.
REAL_WEEKDAY_GAP = {
    "equity_dates": ["2015-10-22", "2015-10-23"],  # Thu -> Fri, delta 1
    "expected_rf": 0.0175 * 1 / 365,  # pre-2015-10-24 benchmark 1.75%
    "effective_date": "2015-08-26",
    "announcement_date": "2015-08-26",
    "raw_rate": 1.75,
    "delta_calendar_days": 1,
}
REAL_WEEKEND_GAP = {
    "equity_dates": ["2015-10-23", "2015-10-26"],  # Fri -> Mon, delta 3
    "expected_rf": 0.015 * 3 / 365,  # effective 2015-10-24 benchmark 1.50%
    "effective_date": "2015-10-24",
    "announcement_date": "2015-10-23",
    "raw_rate": 1.50,
    "delta_calendar_days": 3,
}
REAL_PRE_2015_STEP = {
    # 2014-11-22 (Saturday) changed the benchmark 3.00% -> 2.75%.
    "equity_dates": ["2014-11-21", "2014-11-24"],  # Fri -> Mon, delta 3
    "expected_rf": 0.0275 * 3 / 365,
    "effective_date": "2014-11-22",
    "announcement_date": "2014-11-22",
    "raw_rate": 2.75,
    "delta_calendar_days": 3,
}


@pytest.mark.parametrize(
    "specimen",
    [REAL_WEEKDAY_GAP, REAL_WEEKEND_GAP, REAL_PRE_2015_STEP],
    ids=["weekday_gap", "weekend_gap", "pre_2015_step"],
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


def test_real_fixture_step_boundary_uses_correct_rate():
    """The real PBOC 2015-10-24 changeover: the 2015-10-23 trading day uses
    the prior (1.75%) rate; the next trading day uses the new (1.50%) rate."""
    dates = ["2015-10-22", "2015-10-23", "2015-10-26"]
    provider = _fixture_provider(dates)

    before = provider.latest_announcement_as_of("2015-10-23")
    assert before is not None and before[0] == pd.Timestamp("2015-08-26")
    assert before[2] == 1.75

    after = provider.latest_announcement_as_of("2015-10-26")
    assert after is not None and after[0] == pd.Timestamp("2015-10-24")
    assert after[1] == pd.Timestamp("2015-10-23")
    assert after[2] == 1.50

    frame = provider.get_risk_free(dates[0], dates[-1])
    assert float(frame[RISK_FREE_COL].iloc[1]) == pytest.approx(
        0.0175 * 1 / 365, rel=FORMULA_REL_TOL, abs=FORMULA_ABS_TOL
    )
    assert float(frame[RISK_FREE_COL].iloc[2]) == pytest.approx(
        0.0150 * 3 / 365, rel=FORMULA_REL_TOL, abs=FORMULA_ABS_TOL
    )


def test_real_fixture_post_2015_rate_is_flat_at_1_50_percent():
    """PBOC's benchmark one-year deposit rate has been unchanged at 1.50%
    since 2015-10-24; every post-2015 date must resolve to that record."""
    provider = _fixture_provider(["2024-06-03", "2024-06-04"])

    for query in ("2016-01-04", "2020-06-01", "2024-06-03"):
        found = provider.latest_announcement_as_of(query)
        assert found is not None
        assert found[0] == pd.Timestamp("2015-10-24")
        assert found[1] == pd.Timestamp("2015-10-23")
        assert found[2] == 1.50

    frame = provider.get_risk_free("2024-06-03", "2024-06-04")
    assert float(frame[RISK_FREE_COL].iloc[1]) == pytest.approx(
        0.0150 * 1 / 365, rel=FORMULA_REL_TOL, abs=FORMULA_ABS_TOL
    )


def test_real_fixture_first_and_last_observations_are_pinned():
    body = _load_body()
    observations = body["observations"]
    assert len(observations) == 38
    assert observations[0] == {
        "announcement_date": "1990-04-15",
        "effective_date": "1990-04-15",
        "rate": 10.08,
    }
    assert observations[-1] == {
        "announcement_date": "2015-10-23",
        "effective_date": "2015-10-24",
        "rate": 1.50,
    }
    # The bounded check confirmed exactly one historical event whose public
    # announcement date precedes its effective date.
    differing = [
        o for o in observations if o["announcement_date"] != o["effective_date"]
    ]
    assert differing == [
        {
            "announcement_date": "2015-10-23",
            "effective_date": "2015-10-24",
            "rate": 1.50,
        }
    ]


# ---------------------------------------------------------------------------
# Missing/staleness behavior
# ---------------------------------------------------------------------------
def test_first_date_in_requested_range_is_nan():
    provider = _fixture_provider(["2015-10-22", "2015-10-23", "2015-10-26"])
    frame = provider.get_risk_free("2015-10-22", "2015-10-26")

    assert pd.isna(frame[RISK_FREE_COL].iloc[0])
    assert frame[RISK_FREE_COL].iloc[1:].notna().all()

    # A sub-call that starts mid-sequence still emits NaN for its own first row.
    interior = provider.get_risk_free("2015-10-23", "2015-10-26")
    assert pd.isna(interior[RISK_FREE_COL].iloc[0])
    assert pd.notna(interior[RISK_FREE_COL].iloc[1])


def test_date_before_earliest_effective_date_is_nan_not_an_error():
    provider = _fixture_provider(["1989-01-02", "1989-01-03", "1989-01-04"])
    frame = provider.get_risk_free("1989-01-02", "1989-01-04")

    assert frame[RISK_FREE_COL].isna().all()
    # And the covering-record columns are likewise absent for those rows.
    diagnostics = provider.last_diagnostics
    assert diagnostics["effective_date"].isna().all()
    assert diagnostics["announcement_date"].isna().all()


def test_first_row_never_touches_lookup(monkeypatch):
    """Ordering (direct): the first row of a call is NaN and the step lookup is
    never invoked for it, even though that row has no covering record."""
    provider = _fixture_provider(["1989-01-02", "1989-01-03", "1989-01-04"])

    lookup_calls: list[pd.Timestamp] = []
    original_lookup = ChinaPBOCDepositRiskFreeProvider.latest_announcement_as_of

    def spy_lookup(self, equity_date):
        lookup_calls.append(pd.Timestamp(equity_date).normalize())
        return original_lookup(self, equity_date)

    monkeypatch.setattr(
        ChinaPBOCDepositRiskFreeProvider, "latest_announcement_as_of", spy_lookup
    )

    frame = provider.get_risk_free("1989-01-02", "1989-01-04")
    first = pd.Timestamp("1989-01-02")

    assert pd.isna(frame[RISK_FREE_COL].iloc[0])
    assert first not in lookup_calls
    # Rows 2 and 3 were evaluated once each (and returned no coverage).
    assert lookup_calls == [pd.Timestamp("1989-01-03"), pd.Timestamp("1989-01-04")]


# ---------------------------------------------------------------------------
# No future leakage
# ---------------------------------------------------------------------------
def test_latest_announcement_as_of_never_reads_future():
    records = [
        {"announcement_date": "2020-01-01", "effective_date": "2020-01-01", "rate": 1.0},
        {"announcement_date": "2020-06-01", "effective_date": "2020-06-01", "rate": 9.0},
    ]
    provider = _make_provider(records, ["2020-01-02"])

    assert provider.latest_announcement_as_of("2019-12-31") is None
    assert provider.latest_announcement_as_of("2020-03-01") == (
        pd.Timestamp("2020-01-01"),
        pd.Timestamp("2020-01-01"),
        1.0,
    )
    assert provider.latest_announcement_as_of("2020-06-01")[2] == 9.0

    for query in pd.date_range("2019-12-01", "2020-12-31", freq="D"):
        found = provider.latest_announcement_as_of(query)
        if found is not None:
            assert found[0] <= query
            assert found[1] <= query


def test_future_observation_is_ignored_by_get_risk_free():
    records = [
        {"announcement_date": "2020-01-01", "effective_date": "2020-01-01", "rate": 1.0},
        {"announcement_date": "2020-06-01", "effective_date": "2020-06-01", "rate": 99.0},
    ]
    provider = _make_provider(records, ["2020-05-28", "2020-05-29"])
    frame = provider.get_risk_free("2020-05-28", "2020-05-29")

    assert float(frame[RISK_FREE_COL].iloc[1]) == pytest.approx(
        0.01 * 1 / 365, rel=FORMULA_REL_TOL, abs=FORMULA_ABS_TOL
    )


# ---------------------------------------------------------------------------
# Diagnostic provenance (real fixture)
# ---------------------------------------------------------------------------
def test_real_fixture_diagnostic_provenance():
    dates = REAL_WEEKEND_GAP["equity_dates"]
    provider = _fixture_provider(dates)
    provider.get_risk_free(dates[0], dates[-1])

    diagnostics = provider.last_diagnostics
    assert list(diagnostics.columns) == [
        DATE_COL,
        "preceding_date",
        "delta_calendar_days",
        "effective_date",
        "announcement_date",
        "raw_rate",
        RISK_FREE_COL,
    ]

    first = diagnostics.iloc[0]
    assert first[DATE_COL] == pd.Timestamp(dates[0])
    assert pd.isna(first["preceding_date"])
    assert pd.isna(first["delta_calendar_days"])
    assert pd.isna(first["effective_date"])
    assert pd.isna(first["announcement_date"])
    assert pd.isna(first[RISK_FREE_COL])

    second = diagnostics.iloc[1]
    assert second[DATE_COL] == pd.Timestamp(dates[1])
    assert second["preceding_date"] == pd.Timestamp(dates[0])
    assert second["delta_calendar_days"] == REAL_WEEKEND_GAP["delta_calendar_days"]
    assert second["effective_date"] == pd.Timestamp(REAL_WEEKEND_GAP["effective_date"])
    assert second["announcement_date"] == pd.Timestamp(
        REAL_WEEKEND_GAP["announcement_date"]
    )
    assert second["raw_rate"] == pytest.approx(REAL_WEEKEND_GAP["raw_rate"])
    assert second[RISK_FREE_COL] == pytest.approx(
        REAL_WEEKEND_GAP["expected_rf"], rel=FORMULA_REL_TOL, abs=FORMULA_ABS_TOL
    )


def test_last_diagnostics_is_independent_copy():
    dates = REAL_WEEKEND_GAP["equity_dates"]
    provider = _fixture_provider(dates)
    provider.get_risk_free(dates[0], dates[-1])

    snapshot = provider.last_diagnostics
    snapshot.loc[snapshot.index[0], "raw_rate"] = -1.0
    assert pd.isna(provider.last_diagnostics.iloc[0]["raw_rate"])


# ---------------------------------------------------------------------------
# Trading-calendar acceptance and provenance surfaces
# ---------------------------------------------------------------------------
def test_accepts_trading_calendar_instance():
    calendar = TradingCalendar(["2015-10-22", "2015-10-23", "2015-10-26"])
    provider = ChinaPBOCDepositRiskFreeProvider(
        _load_observations(), trading_dates=calendar
    )
    assert list(provider.trading_dates) == [
        pd.Timestamp("2015-10-22"),
        pd.Timestamp("2015-10-23"),
        pd.Timestamp("2015-10-26"),
    ]
    frame = provider.get_risk_free("2015-10-22", "2015-10-26")
    assert len(frame) == 3


def test_provenance_surfaces_are_independent_copies():
    provider = _fixture_provider(["2015-10-22"])
    assert len(provider.announcement_dates) == 38
    assert len(provider.effective_dates) == 38
    assert len(provider.rates) == 38

    # Each access returns a fresh, independently-owned object.
    assert provider.announcement_dates is not provider.announcement_dates
    assert provider.rates is not provider.rates

    rates = provider.rates
    rates[0] = -123.0
    assert provider.rates[0] != -123.0


# ---------------------------------------------------------------------------
# Configuration / input hygiene
# ---------------------------------------------------------------------------
def test_frozen_default_day_count_basis_is_365():
    assert DEFAULT_DAY_COUNT_BASIS == 365
    provider = _fixture_provider(["2015-10-22"])
    assert provider.day_count_basis == 365


def test_day_count_basis_is_configurable_and_validated():
    records = [
        {"announcement_date": "2019-01-01", "effective_date": "2019-01-01", "rate": 5.00}
    ]
    provider = _make_provider(
        records, ["2019-01-02", "2019-07-01"], day_count_basis=360
    )
    actual = float(
        provider.get_risk_free("2019-01-02", "2019-07-01")[RISK_FREE_COL].iloc[1]
    )
    assert actual == pytest.approx(
        0.05 * 180 / 360, rel=FORMULA_REL_TOL, abs=FORMULA_ABS_TOL
    )

    for bad in (0, -1, 1.5, True):
        with pytest.raises(ValueError, match="positive integer"):
            _make_provider(records, ["2019-01-02"], day_count_basis=bad)


def test_duplicate_effective_dates_are_rejected():
    records = [
        {"announcement_date": "2020-01-01", "effective_date": "2020-01-01", "rate": 1.0},
        {"announcement_date": "2020-01-02", "effective_date": "2020-01-01", "rate": 2.0},
    ]
    with pytest.raises(ValueError, match="duplicate effective dates"):
        _make_provider(records, ["2020-01-02"])


def test_empty_or_malformed_source_is_rejected():
    with pytest.raises(ValueError, match="no rate observations"):
        _make_provider(
            [{"announcement_date": "2020-01-01", "effective_date": "2020-01-01", "rate": np.nan}],
            ["2020-01-02"],
        )
    with pytest.raises(ValueError, match="missing required column"):
        _make_provider(
            [{"effective_date": "2020-01-01", "rate": 1.0}], ["2020-01-02"]
        )
    with pytest.raises(TypeError, match="DataFrame"):
        ChinaPBOCDepositRiskFreeProvider(
            [{"effective_date": "2020-01-01", "rate": 1.0}],  # type: ignore[arg-type]
            trading_dates=["2020-01-02"],
        )


def test_blank_rate_rows_are_not_observations():
    records = [
        {"announcement_date": "2020-01-01", "effective_date": "2020-01-01", "rate": 1.0},
        {"announcement_date": "2020-06-01", "effective_date": "2020-06-01", "rate": None},
    ]
    provider = _make_provider(records, ["2020-07-01", "2020-07-02"])
    assert provider.latest_announcement_as_of("2020-07-01")[2] == 1.0


def test_empty_requested_window_returns_empty_schema_frame():
    provider = _fixture_provider(["2015-10-22", "2015-10-23"])
    frame = provider.get_risk_free("2016-01-01", "2016-01-05")
    assert frame.empty
    validate_panel(frame, RISK_FREE_SCHEMA, name="empty china risk-free")


def test_provider_does_not_mutate_supplied_inputs():
    source = _load_observations()
    source_copy = source.copy(deep=True)
    trading_dates = ["2015-10-22", "2015-10-23"]

    provider = ChinaPBOCDepositRiskFreeProvider(
        source, trading_dates=trading_dates
    )
    provider.get_risk_free(trading_dates[0], trading_dates[-1])

    pd.testing.assert_frame_equal(source, source_copy)
    assert trading_dates == ["2015-10-22", "2015-10-23"]


# ---------------------------------------------------------------------------
# Fixture provenance / bounded-check evidence
# ---------------------------------------------------------------------------
def test_fixture_provenance_and_bounded_check_evidence():
    body = _load_body()
    manifest = json.loads(
        (_FIXTURE_DIR / "manifest.json").read_text(encoding="utf-8")
    )

    assert manifest["_provenance"]["live_recorded"] is True
    assert manifest["_provenance"]["provenance_class"] == (
        "ECONOMIC-DEFINITION REPLICATION"
    )
    assert any(
        c.startswith("CONSTRUCTED")
        for c in manifest["_provenance"]["evidence_categories"]
    )
    assert body["_provenance"]["exact_source_replication"] is False

    # The independent cross-check confirms the last real change exactly.
    confirms = manifest["independent_cross_check"]["confirms"]
    last = body["observations"][-1]
    assert confirms["announcement_date"] == last["announcement_date"] == "2015-10-23"
    assert confirms["effective_date"] == last["effective_date"] == "2015-10-24"
    assert confirms["rate"] == last["rate"] == 1.50

    # The body hash recorded in the manifest matches the committed bytes.
    raw_bytes = _FIXTURE_JSON.read_bytes()
    recording = manifest["recordings"][_FIXTURE_JSON.name]
    assert recording["body_sha256"] == hashlib.sha256(raw_bytes).hexdigest()
    assert recording["n_observations"] == len(body["observations"]) == 38

    # P5B-1's required bounded check is persisted, and the
    # transformation-convention boundary it could not resolve is preserved.
    bounded = manifest["bounded_check"]
    assert bounded["performed"] is True
    findings = bounded["findings"]
    assert findings["risk_free_instrument"]["result"] == "CONFIRMED"
    assert findings["pboc_rate_history"]["result"] == "CONFIRMED"
    assert findings["announcement_vs_effective"]["result"] == "CONFIRMED"
    assert findings["pro_rate_vs_compound"]["result"] == "NOT ESTABLISHED"
    assert "NOT CERTIFIED" in findings["pro_rate_vs_compound"]["disposition"]
    assert findings["ep_formation_cadence"]["result"] == "CONFIRMED"
    assert findings["cumulative_ytd_vs_ttm"]["result"] == "NOT ESTABLISHED"


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
