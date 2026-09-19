"""Phase 5A, P5A-3: independent Gate A hand verification.

This module owns **no production code**. It reconstructs exactly one real,
non-``NaN`` Gate A ``MKT`` observation from P5A-2's committed *raw* recorded
fixtures using plain arithmetic, then asserts the result matches the single
``MKT`` value P5A-2 committed to Artifact A. Calling
``compute_market_excess_return`` again and comparing its output to itself
would only confirm determinism; this test instead re-derives the number from
the raw evidence.

Chosen date: **2026-06-16**, the first non-``NaN`` ``MKT`` observation in
the committed Gate A output. All three frozen Gate A names (AAPL, MSFT, JPM)
are eligible on that date, so the reconstruction exercises the full
value-weighting chain with no exclusion. Its preceding trading date is
2026-06-15, giving ``delta_calendar_days == 1`` and a same-day DGS3MO
publication -- the simplest possible real risk-free interval. (If no
all-three-eligible date had existed, the best available date would have been
used and the exclusion stated here.)

Independence boundary (explicit; the reviewer confirm this by reading this
file's source, as required by the P5A-3 task spec): this file reads *raw*
fixtures only -- the raw Tiingo EOD JSON, the raw Tiingo daily-fundamentals
JSON, and the raw FRED ``DGS3MO`` CSV body -- plus exactly **one** processed
value, Artifact A's final ``MKT`` for 2026-06-16, as the comparison target.
It never reads Artifact B (P5A-2's own already-computed diagnostic trace:
excluded names, raw lagged-mcap trace, or risk-free columns) and never
imports or calls any ``smart_beta`` production module, including
``compute_market_excess_return`` and every ``smart_beta.benchmarks.capm``
private helper.

Evidence taxonomy: the inputs are ``LIVE-RECORDED`` raw fixtures; the
reconstruction below is the ``HAND-VERIFIED`` step. A single-point
arithmetic check is not a statistical claim.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
import math
import urllib.request
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Frozen Gate A constants
# ---------------------------------------------------------------------------
GATE_A_TICKERS = ("AAPL", "MSFT", "JPM")

#: The one date independently reconstructed (see module docstring).
VERIFIED_DATE = "2026-06-16"
PRECEDING_DATE = "2026-06-15"

#: Frozen relative tolerance. The reconstruction is plain float64 arithmetic
#: over the same real inputs; the committed Artifact-A value is JSON-rounded
#: at roughly machine precision, and the observed relative residual is
#: ~2.3e-14. 1e-9 matches this project's prior certification
#: hand-verifications and is many orders of magnitude above float
#: accumulation here; it is tight enough that a substituted ``/252``
#: compounding or a wrong weight would fail loudly (the ``/252`` variant for
#: this Rf alone differs at the ~1e-8 level).
REL_TOL = 1e-9

#: Frozen, hand-computed literals (derived with independent plain-Python
#: arithmetic from the raw fixtures -- never by calling any production
#: helper). They pin the raw evidence itself, so the reconstruction cannot
#: silently drift if a fixture changes.
EXPECTED_ADJ_RET = {
    "AAPL": 0.009513528102017332,
    "MSFT": -0.014833900340204154,
    "JPM": 0.036756418284283,
}
EXPECTED_LAG_MCAP = {
    "AAPL": 4351783518800.0,
    "MSFT": 2968469479421.76,
    "JPM": 861432140574.9999,
}
EXPECTED_WEIGHTS = {
    "AAPL": 0.5318933013156744,
    "MSFT": 0.362818836103282,
    "JPM": 0.1052878625810436,
}
EXPECTED_RM = 0.003548168130323437
EXPECTED_RF = 0.00010383561643835617
EXPECTED_MKT = 0.003444332513885081

# Raw DGS3MO evidence actually recorded for this interval.
EXPECTED_DGS3MO_SOURCE_DATE = "2026-06-16"
EXPECTED_DGS3MO_RAW_YIELD = 3.79
EXPECTED_DELTA_CALENDAR_DAYS = 1

# ---------------------------------------------------------------------------
# Raw fixture paths (no production artifact is ever imported)
# ---------------------------------------------------------------------------
_TESTS_DIR = Path(__file__).resolve().parent
_TIINGO_DIR = _TESTS_DIR / "fixtures" / "tiingo" / "phase5a_gate_a"
_FRED_FIXTURE = (
    _TESTS_DIR
    / "fixtures"
    / "risk_free"
    / "treasury"
    / "dgs3mo_2025-09-01_2026-09-16.json"
)
#: The single processed comparison value lives here; only the ``MKT`` field
#: for ``VERIFIED_DATE`` is ever read (see ``_pipeline_mkt_for``).
_ARTIFACT_A = _TESTS_DIR.parent / "docs" / "phase5a" / "gate_a" / "artifact_a_market_factor.json"

_EOD_FILENAME = {
    ticker: f"{ticker.lower()}_eod_prices_2026-06-05_2026-09-15.json"
    for ticker in GATE_A_TICKERS
}
_FUNDAMENTALS_FILENAME = {
    ticker: f"{ticker.lower()}_fundamentals_daily_2026-06-15_2026-09-15.json"
    for ticker in GATE_A_TICKERS
}


@pytest.fixture(autouse=True)
def _forbid_live_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Structural guarantee of zero live network calls: reading the committed
    raw fixtures is the only I/O this module performs."""

    def _boom(*args: object, **kwargs: object) -> object:
        raise AssertionError(
            "P5A-3 is a pure offline hand verification; a live network call "
            "was attempted."
        )

    monkeypatch.setattr(urllib.request, "urlopen", _boom)


# ---------------------------------------------------------------------------
# Raw-fixture readers (stdlib only -- no pandas/numpy, no smart_beta)
# ---------------------------------------------------------------------------
def _read_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def _index_by_date(rows: list[dict]) -> dict[str, dict]:
    """Index raw Tiingo rows (ISO ``...Z`` date strings) by calendar date."""
    return {str(row["date"])[:10]: row for row in rows}


def _read_eod(ticker: str) -> dict[str, dict]:
    return _index_by_date(_read_json(_TIINGO_DIR / _EOD_FILENAME[ticker]))


def _read_fundamentals(ticker: str) -> dict[str, dict]:
    return _index_by_date(
        _read_json(_TIINGO_DIR / _FUNDAMENTALS_FILENAME[ticker])
    )


def _read_fred_observations() -> list[tuple[str, float]]:
    """Parse the verbatim FRED CSV body, dropping unpublished (blank) rows.

    Blank values are FRED's representation of a market holiday; they are not
    observations and must never be treated as zero.
    """
    body = _read_json(_FRED_FIXTURE)
    reader = csv.DictReader(io.StringIO(body["raw_csv"]))
    observations: list[tuple[str, float]] = []
    for row in reader:
        value = (row.get("DGS3MO") or "").strip()
        if value == "":
            continue
        observations.append((row["observation_date"], float(value)))
    return observations


def _latest_at_or_before(
    observations: list[tuple[str, float]], equity_date: str
) -> tuple[str, float] | None:
    """Most recent DGS3MO observation dated at or before ``equity_date``.

    Strictly backward, so a future-dated observation can never leak in.
    """
    best: tuple[str, float] | None = None
    for source_date, value in observations:
        if source_date <= equity_date:
            if best is None or source_date > best[0]:
                best = (source_date, value)
    return best


def _pipeline_mkt_for(equity_date: str) -> float | None:
    """The one processed value this module is permitted to read: Artifact A's
    final ``MKT`` for ``equity_date``. No other Artifact-A field and no
    Artifact-B field is touched."""
    for row in _read_json(_ARTIFACT_A):
        if str(row["date"])[:10] == equity_date:
            value = row["MKT"]
            return None if value is None else float(value)
    raise AssertionError(f"Artifact A has no row for {equity_date}")


def _relative_residual(actual: float, expected: float) -> float:
    if expected == 0.0:
        return abs(actual - expected)
    return abs(actual - expected) / abs(expected)


# ---------------------------------------------------------------------------
# The independent reconstruction
# ---------------------------------------------------------------------------
def test_hand_reconstruction_matches_committed_gate_a_mkt() -> None:
    # ---- load raw evidence -------------------------------------------------
    eod = {ticker: _read_eod(ticker) for ticker in GATE_A_TICKERS}
    fundamentals = {ticker: _read_fundamentals(ticker) for ticker in GATE_A_TICKERS}
    fred = _read_fred_observations()
    pipeline_mkt = _pipeline_mkt_for(VERIFIED_DATE)

    assert pipeline_mkt is not None and math.isfinite(pipeline_mkt), (
        f"committed Artifact-A MKT for {VERIFIED_DATE} is not a finite "
        f"number ({pipeline_mkt!r}); nothing to compare against"
    )

    # ---- step 0: preceding trading date, from the raw EOD panel itself -----
    aapl_dates = sorted(eod["AAPL"])
    assert VERIFIED_DATE in aapl_dates
    preceding_index = aapl_dates.index(VERIFIED_DATE) - 1
    assert preceding_index >= 0, "no preceding trading row in the raw EOD fixture"
    preceding_date = aapl_dates[preceding_index]
    assert preceding_date == PRECEDING_DATE, (
        f"raw EOD rows place {preceding_date} immediately before "
        f"{VERIFIED_DATE}, expected {PRECEDING_DATE}"
    )

    # ---- step 1: eligibility ----------------------------------------------
    # Gate A uses bottom_mcap_exclude_pct == 0.0 and every frozen name listed
    # far longer than min_listing_age_months (12), so
    # USZeroVolumeTradabilityPolicy reduces to its volume check:
    # ``is_zero_volume == (volume == 0)``. All three raw rows carry real
    # volume, so all three are eligible -- stated explicitly here rather than
    # re-deriving the whole policy.
    eligible = [
        ticker
        for ticker in GATE_A_TICKERS
        if float(eod[ticker][VERIFIED_DATE]["volume"]) != 0.0
    ]
    assert eligible == list(GATE_A_TICKERS), (
        f"expected all of {GATE_A_TICKERS} eligible on {VERIFIED_DATE} from "
        f"the raw volume evidence, got {eligible}"
    )

    # ---- steps 2-4: lagged market cap and adjusted return per name --------
    lag_mcap: dict[str, float] = {}
    adj_ret: dict[str, float] = {}
    for ticker in eligible:
        row = eod[ticker][VERIFIED_DATE]
        prev = eod[ticker][preceding_date]

        # No corporate action other than ordinary cash dividends is present
        # for these rows; assert it rather than assume it, so a future split
        # would fail this test loudly instead of silently mispricing.
        assert float(row["splitFactor"]) == 1.0, (
            f"{ticker} has splitFactor={row['splitFactor']} on {VERIFIED_DATE}; "
            "the dividend-only hand adjustment below is invalid"
        )

        # Lagged market cap = the prior trading day's total_mcap, read
        # straight from the raw daily-fundamentals fixture.
        lag_mcap[ticker] = float(fundamentals[ticker][preceding_date]["marketCap"])

        # Adjusted (total) return from raw close + the same row's cash
        # dividend: (close_t + divCash_t) / close_{t-1} - 1. This is exactly
        # the trusted adjustment chain's result for a no-split row.
        adj_ret[ticker] = (
            float(row["close"]) + float(row["divCash"])
        ) / float(prev["close"]) - 1.0

        assert _relative_residual(adj_ret[ticker], EXPECTED_ADJ_RET[ticker]) <= REL_TOL, (
            f"{ticker} adj_ret {adj_ret[ticker]!r} != frozen expected "
            f"{EXPECTED_ADJ_RET[ticker]!r}"
        )
        assert _relative_residual(lag_mcap[ticker], EXPECTED_LAG_MCAP[ticker]) <= REL_TOL, (
            f"{ticker} lagged mcap {lag_mcap[ticker]!r} != frozen expected "
            f"{EXPECTED_LAG_MCAP[ticker]!r}"
        )

    # ---- step 3: normalized weights ---------------------------------------
    total_lag_mcap = sum(lag_mcap.values())
    weights = {ticker: lag_mcap[ticker] / total_lag_mcap for ticker in eligible}
    for ticker in eligible:
        assert _relative_residual(weights[ticker], EXPECTED_WEIGHTS[ticker]) <= REL_TOL, (
            f"{ticker} normalized weight {weights[ticker]!r} != frozen "
            f"expected {EXPECTED_WEIGHTS[ticker]!r}"
        )

    # ---- step 5: Rm = weighted sum of constituent adj_ret -----------------
    rm = sum(weights[ticker] * adj_ret[ticker] for ticker in eligible)
    assert _relative_residual(rm, EXPECTED_RM) <= REL_TOL, (
        f"hand Rm {rm!r} != frozen expected {EXPECTED_RM!r}"
    )

    # ---- step 6: risk-free, frozen (y/100) * (delta_calendar_days/365) -----
    observation = _latest_at_or_before(fred, VERIFIED_DATE)
    assert observation is not None, (
        f"raw DGS3MO fixture has no published observation at or before "
        f"{VERIFIED_DATE}"
    )
    source_date, raw_yield = observation
    assert source_date == EXPECTED_DGS3MO_SOURCE_DATE
    assert raw_yield == pytest.approx(EXPECTED_DGS3MO_RAW_YIELD)

    delta_calendar_days = (
        dt.date.fromisoformat(VERIFIED_DATE) - dt.date.fromisoformat(preceding_date)
    ).days
    assert delta_calendar_days == EXPECTED_DELTA_CALENDAR_DAYS

    rf = (raw_yield / 100.0) * (delta_calendar_days / 365.0)
    assert _relative_residual(rf, EXPECTED_RF) <= REL_TOL, (
        f"hand Rf {rf!r} != frozen expected {EXPECTED_RF!r}"
    )

    # ---- step 7: MKT = Rm - Rf, compared with P5A-2's committed value -----
    hand_mkt = rm - rf
    assert _relative_residual(hand_mkt, EXPECTED_MKT) <= REL_TOL, (
        f"hand MKT {hand_mkt!r} != frozen expected {EXPECTED_MKT!r}"
    )

    residual = abs(hand_mkt - pipeline_mkt)
    relative = _relative_residual(hand_mkt, pipeline_mkt)
    assert relative <= REL_TOL, (
        "P5A-3 hand-verification divergence (BLOCKING FINDING):\n"
        f"  date                = {VERIFIED_DATE}\n"
        f"  eligible names      = {eligible}\n"
        f"  lagged mcaps        = {lag_mcap}\n"
        f"  adj_ret             = {adj_ret}\n"
        f"  normalized weights  = {weights}\n"
        f"  hand Rm             = {rm!r}\n"
        f"  DGS3MO source date  = {source_date}\n"
        f"  DGS3MO raw yield    = {raw_yield!r}\n"
        f"  delta_calendar_days = {delta_calendar_days}\n"
        f"  hand Rf             = {rf!r}\n"
        f"  hand MKT            = {hand_mkt!r}\n"
        f"  pipeline MKT        = {pipeline_mkt!r}\n"
        f"  abs residual        = {residual!r}\n"
        f"  relative residual   = {relative!r}\n"
        f"  tolerance           = {REL_TOL!r}"
    )
