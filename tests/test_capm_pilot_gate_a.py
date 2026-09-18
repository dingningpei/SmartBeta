"""Gate A orchestration tests (Phase 5A, P5A-2).

Everything here is offline: the real, live-recorded Tiingo fixtures under
``tests/fixtures/tiingo/phase5a_gate_a/`` and P5A-1's FRED fixture are
replayed through the existing, unmodified ``replay_transport``. An autouse
tripwire replaces ``urllib.request.urlopen`` so an accidental live call fails
loudly (``test_no_live_network_calls`` is therefore structural, not a single
test).

The module under test owns no mapping/adjustment/weight logic; these tests
prove that structural fact mechanically (the literal source-grep guard), that
the orchestration calls the one authoritative ``MKT`` function (spy test),
that the sanctioned Artifact-A decomposition invariant holds, and that the RF
date-grid ownership split fails closed on a mismatched grid.

Frozen Gate A window: 2026-06-15 .. 2026-09-15.

**Gate A is BLOCKED.** The frozen candidate universe is AAPL, MSFT, GOOGL, but
the required GOOGL daily-fundamentals / market-cap endpoint returns a
persistent HTTP 400 plan-tier "DOW 30" entitlement restriction. The live
fixtures were subsequently overwritten by HTTP 429 error bodies and are not
reconstructed. Consequently every test that replays the real Gate A fixtures
is skipped with an explicit reason; the structural source-grep guard and the
blocked-disposition assertions still run. The legacy ``RUN_UNIVERSE`` below is
the entitlement-constrained two-name partial-run universe and is NOT the frozen
Gate A universe.
"""

from __future__ import annotations

import json
import re
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from smart_beta.pipelines import capm_pilot
from smart_beta.pipelines.capm_pilot import (
    INSUFFICIENT_OBSERVATIONS_STATUS,
    MINIMUM_OBSERVATIONS,
    RiskFreeDateGridMismatchError,
    derive_trading_dates,
    run_capm_pilot,
)
from smart_beta.pit.view import PointInTimeView
from smart_beta.research_inputs.risk_free import ConstantRiskFreeProvider
from smart_beta.research_inputs.risk_free_treasury import (
    TreasuryBillRiskFreeProvider,
)
from smart_beta.vendors.tiingo.client import (
    TiingoAPIError,
    TiingoClient,
    replay_transport,
)
from smart_beta.vendors.tiingo.source import TiingoPITSource

# ---------------------------------------------------------------------------
# Frozen Gate A constants
# ---------------------------------------------------------------------------
GATE_A_START = "2026-06-15"
GATE_A_END = "2026-09-15"
CANDIDATE_UNIVERSE = ("AAPL", "MSFT", "GOOGL")
RUN_UNIVERSE = ("AAPL", "MSFT")
EXPECTED_TRADING_DATE_COUNT = 64
EXPECTED_NON_NAN_MKT = 63
DECOMPOSITION_REL_TOL = 1e-9

_TESTS_DIR = Path(__file__).resolve().parent
_FIXTURE_DIR = _TESTS_DIR / "fixtures" / "tiingo" / "phase5a_gate_a"
_FRED_FIXTURE = (
    _TESTS_DIR
    / "fixtures"
    / "risk_free"
    / "treasury"
    / "dgs3mo_2025-09-01_2026-09-16.json"
)
_ARTIFACT_DIR = _TESTS_DIR.parent / "docs" / "phase5a" / "gate_a"
_PARTIAL_RUN_DIR = _ARTIFACT_DIR / "partial_run_entitlement_limited_not_gate_a"

_GATE_A_NOT_RUN_MARKERS = (
    "artifact_a_market_factor.NOT_RUN.txt",
    "artifact_b_constituent_diagnostics.NOT_RUN.txt",
    "artifact_b_risk_free_diagnostics.NOT_RUN.txt",
    "artifact_c_statistical_summary.NOT_RUN.txt",
)

_GATE_A_BLOCKED_PREFIX = (
    "Gate A BLOCKED: no valid live-recorded Gate A fixture set exists. "
    "The frozen universe (AAPL, MSFT, GOOGL) cannot execute because "
    "GET /tiingo/fundamentals/GOOGL/daily returns a persistent HTTP 400 "
    "plan-tier 'DOW 30' entitlement restriction; the valid live fixtures "
    "were later overwritten by HTTP 429 bodies and are not reconstructed. "
)

_ARTIFACTS = {
    "a_csv": "artifact_a_market_factor.csv",
    "a_json": "artifact_a_market_factor.json",
    "b_constituent_csv": "artifact_b_constituent_diagnostics.csv",
    "b_constituent_json": "artifact_b_constituent_diagnostics.json",
    "b_risk_free_csv": "artifact_b_risk_free_diagnostics.csv",
    "b_risk_free_json": "artifact_b_risk_free_diagnostics.json",
    "c_json": "artifact_c_statistical_summary.json",
}

_CAPM_PRIVATE_SYMBOLS = (
    "_market_factor",
    "_value_weighted_returns",
    "_value_weighted_by",
    "_load_pit_panel",
)


# ---------------------------------------------------------------------------
# Fixture / replay helpers
# ---------------------------------------------------------------------------
def _manifest() -> dict:
    return json.loads((_FIXTURE_DIR / "manifest.json").read_text(encoding="utf-8"))


def _gate_a_recording_blockers() -> list[str]:
    """Names every reason the committed fixture set is not a valid Gate A
    recording. A valid set would be all-200 for the frozen universe; the
    current set is a blocked recording (all HTTP 429) and the manifest says so.
    """
    manifest = _manifest()
    provenance = manifest.get("_provenance", {})
    blockers: list[str] = []
    if provenance.get("gate_a_disposition") == "BLOCKED":
        blockers.append("manifest _provenance.gate_a_disposition == BLOCKED")
    for filename, entry in manifest.get("recordings", {}).items():
        status = int(entry.get("status_code", 0))
        if status != 200:
            blockers.append(f"{filename}: status_code {status}")
    return blockers


def _require_valid_gate_a_fixtures() -> None:
    """Skip (never silently pass) any test that needs the real Gate A
    fixture replay while the recording is blocked."""
    blockers = _gate_a_recording_blockers()
    if blockers:
        pytest.skip(_GATE_A_BLOCKED_PREFIX + "; ".join(blockers))


def _body(filename: str):
    return json.loads((_FIXTURE_DIR / filename).read_text(encoding="utf-8"))


def _offline_client() -> TiingoClient:
    """A fixture-fed client: one path -> one recorded response body."""
    _require_valid_gate_a_fixtures()
    recordings: dict[str, tuple[int, object]] = {}
    for filename, entry in _manifest()["recordings"].items():
        recordings[str(entry["url_path"])] = (
            int(entry["status_code"]),
            _body(filename),
        )
    return TiingoClient(transport=replay_transport(recordings))


def _view(client: TiingoClient, tickers=CANDIDATE_UNIVERSE) -> PointInTimeView:
    return PointInTimeView(TiingoPITSource(list(tickers), client=client))


def _fred_frame() -> pd.DataFrame:
    import io

    body = json.loads(_FRED_FIXTURE.read_text(encoding="utf-8"))
    return pd.read_csv(io.StringIO(body["raw_csv"]))


def _provider(client: TiingoClient, start: str, end: str, tickers=RUN_UNIVERSE):
    dates = derive_trading_dates(_view(client, tickers), start, end)
    return TreasuryBillRiskFreeProvider(_fred_frame(), trading_dates=dates)


def _run(
    start: str = GATE_A_START,
    end: str = GATE_A_END,
    tickers=RUN_UNIVERSE,
):
    client = _offline_client()
    return run_capm_pilot(
        tickers,
        start,
        end,
        tiingo_client=client,
        risk_free=_provider(client, start, end, tickers),
    )


@pytest.fixture(scope="module")
def gate_a_result():
    _require_valid_gate_a_fixtures()
    return _run()


@pytest.fixture(autouse=True)
def _forbid_live_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*args: object, **kwargs: object) -> object:
        raise AssertionError(
            "Test attempted a live network call; inject replay_transport instead."
        )

    monkeypatch.setattr(urllib.request, "urlopen", _boom)


def _committed_factor() -> pd.DataFrame:
    frame = pd.DataFrame(
        json.loads(
            (_ARTIFACT_DIR / _ARTIFACTS["a_json"]).read_text(encoding="utf-8")
        )
    )
    frame["date"] = pd.to_datetime(frame["date"])
    return frame


# ---------------------------------------------------------------------------
# derive_trading_dates
# ---------------------------------------------------------------------------
def test_derive_trading_dates_is_the_frozen_window_grid():
    client = _offline_client()
    dates = derive_trading_dates(_view(client), GATE_A_START, GATE_A_END)

    assert isinstance(dates, pd.DatetimeIndex)
    assert dates.name == "date"
    assert dates.is_unique
    assert dates.is_monotonic_increasing
    assert len(dates) == EXPECTED_TRADING_DATE_COUNT
    assert dates[0] == pd.Timestamp(GATE_A_START)
    assert dates[-1] == pd.Timestamp(GATE_A_END)
    # Real NYSE closures inside the window are absent (holiday rows are not
    # trading dates merely because a calendar day exists).
    for holiday in ("2026-06-19", "2026-07-03", "2026-09-07"):
        assert pd.Timestamp(holiday) not in dates
    assert pd.Timestamp("2026-06-22") in dates
    # Every date is a weekday.
    assert all(ts.weekday() < 5 for ts in dates)


def test_derive_trading_dates_matches_committed_artifact_dates(gate_a_result):
    committed_dates = pd.DatetimeIndex(_committed_factor()["date"])
    assert pd.DatetimeIndex(gate_a_result.trading_dates).equals(committed_dates)


def test_risk_free_grid_matches_authoritative_grid(gate_a_result):
    """Positive date-grid invariant: the provider emits exactly the
    authoritative grid, so ``run_capm_pilot`` does not raise."""
    assert (
        pd.DatetimeIndex(gate_a_result.risk_free_diagnostics["date"])
        .sort_values()
        .equals(pd.DatetimeIndex(gate_a_result.trading_dates).sort_values())
    )


def test_mismatched_trading_dates_raise_fail_closed():
    """A provider built from a real date list with one date removed must
    raise ``RiskFreeDateGridMismatchError`` rather than let a silent NaN
    reach ``MKT``."""
    client = _offline_client()
    full = derive_trading_dates(_view(client), GATE_A_START, GATE_A_END)
    reduced = full.delete(len(full) - 2)
    assert not reduced.equals(full)

    provider = TreasuryBillRiskFreeProvider(
        _fred_frame(), trading_dates=reduced
    )
    with pytest.raises(RiskFreeDateGridMismatchError):
        run_capm_pilot(
            RUN_UNIVERSE,
            GATE_A_START,
            GATE_A_END,
            tiingo_client=client,
            risk_free=provider,
        )


# ---------------------------------------------------------------------------
# Artifact A / C schema and consistency
# ---------------------------------------------------------------------------
def test_gate_a_artifacts_are_explicitly_not_run():
    """The required Gate A artifacts do not exist as results: each carries an
    explicit NOT RUN marker, and the machine-readable disposition says so."""
    for marker in _GATE_A_NOT_RUN_MARKERS:
        path = _ARTIFACT_DIR / marker
        assert path.is_file(), marker
        assert "NOT RUN" in path.read_text(encoding="utf-8")

    disposition = json.loads(
        (_ARTIFACT_DIR / "GATE_A_DISPOSITION.json").read_text(encoding="utf-8")
    )
    assert disposition["disposition"] == "BLOCKED"
    assert disposition["gate_a_pass"] is False
    assert disposition["real_three_name_execution"] == "NOT RUN"
    assert disposition["artifacts"] == {"A": "NOT RUN", "B": "NOT RUN", "C": "NOT RUN"}
    assert disposition["next_task"]["p5a_3"] == "NOT STARTED"
    blocking_ids = {finding["id"] for finding in disposition["blocking_findings"]}
    assert blocking_ids == {"GATE-A-1"}
    operational_ids = {
        finding["id"] for finding in disposition["separate_operational_findings"]
    }
    assert operational_ids == {"GATE-A-429"}


def test_partial_run_evidence_is_preserved_and_labeled_not_gate_a():
    """The historical two-name partial run is retained byte-for-byte but is
    explicitly labeled as NOT Gate A evidence."""
    assert _PARTIAL_RUN_DIR.is_dir()
    for name in (
        "artifact_a_market_factor.csv",
        "artifact_a_market_factor.json",
        "artifact_b_constituent_diagnostics.csv",
        "artifact_b_risk_free_diagnostics.csv",
        "artifact_c_statistical_summary.json",
    ):
        assert (_PARTIAL_RUN_DIR / name).is_file(), name
    readme = (_PARTIAL_RUN_DIR / "README.md").read_text(encoding="utf-8")
    assert "NOT Gate A evidence" in readme


def test_factor_schema_conforms(gate_a_result):
    factor = gate_a_result.factor
    assert list(factor.columns) == [
        "date",
        "universe_count",
        "market_return",
        "risk_free_return",
        "MKT",
    ]
    assert len(factor) == EXPECTED_TRADING_DATE_COUNT
    assert factor["date"].is_monotonic_increasing
    assert pd.api.types.is_integer_dtype(factor["universe_count"])
    for column in ("market_return", "risk_free_return", "MKT"):
        assert pd.api.types.is_float_dtype(factor[column])
    # First row is NaN by the frozen first-observation / lag rules.
    assert pd.isna(factor["MKT"].iloc[0])
    assert pd.isna(factor["risk_free_return"].iloc[0])
    assert factor["MKT"].notna().sum() == EXPECTED_NON_NAN_MKT


def test_universe_count_is_an_upper_bound(gate_a_result):
    counts = gate_a_result.factor["universe_count"]
    assert (counts >= 1).all()
    # Never more than the entitled run universe; this is an upper bound, not
    # a reproduction of the downstream value-weighted exclusion chain.
    assert (counts <= len(RUN_UNIVERSE)).all()


def test_statistical_summary_schema(gate_a_result):
    summary = gate_a_result.statistics
    assert summary["status"] == "RUN"
    assert summary["n"] == EXPECTED_NON_NAN_MKT
    assert summary["n"] >= MINIMUM_OBSERVATIONS
    assert summary["newey_west_lags"] == 6
    assert isinstance(summary["mean"], float)
    assert isinstance(summary["std"], float)
    assert np.isfinite(summary["newey_west_tstat"])

    mkt = gate_a_result.factor["MKT"].dropna()
    assert summary["mean"] == pytest.approx(float(mkt.mean()))
    assert summary["std"] == pytest.approx(float(mkt.std(ddof=1)))


def test_insufficient_observations_summary_constructed():
    """CONSTRUCTED (not live evidence): the <20-observation branch of the
    required Artifact-C summary is exercised directly, without the lost live
    Gate A fixtures."""
    from smart_beta.config.settings import DEFAULT_SETTINGS

    summary = capm_pilot._statistical_summary(
        pd.Series([0.01, -0.02, float("nan")]), DEFAULT_SETTINGS
    )
    assert summary["n"] == 2
    assert summary["status"] == INSUFFICIENT_OBSERVATIONS_STATUS
    assert summary["mean"] is None
    assert summary["std"] is None
    assert summary["newey_west_tstat"] is None


def test_insufficient_observations_reports_not_run():
    client = _offline_client()
    start, end = "2026-06-15", "2026-06-22"
    dates = derive_trading_dates(_view(client, ("AAPL",)), start, end)
    provider = ConstantRiskFreeProvider(0.0, dates=dates)
    result = run_capm_pilot(
        ("AAPL",),
        start,
        end,
        tiingo_client=client,
        risk_free=provider,
    )
    assert result.statistics["n"] < MINIMUM_OBSERVATIONS
    assert result.statistics["status"] == INSUFFICIENT_OBSERVATIONS_STATUS
    assert result.statistics["mean"] is None
    assert result.statistics["std"] is None
    assert result.statistics["newey_west_tstat"] is None


# ---------------------------------------------------------------------------
# Artifact B: named exclusions and diagnostics
# ---------------------------------------------------------------------------
def test_excluded_observation_is_named_with_reason(gate_a_result):
    diagnostics = gate_a_result.diagnostics
    excluded = diagnostics.loc[diagnostics["exclusion_reason"].notna()]
    assert not excluded.empty, "expected a real tradability exclusion to name"
    # MSFT is the real, in-window bottom-cap tradability exclusion.
    msft = excluded.loc[excluded["stock_id"] == "MSFT"]
    assert not msft.empty
    assert (msft["is_tradable"] == False).all()  # noqa: E712
    assert set(msft["exclusion_reason"]) == {"tradability_policy"}


def test_diagnostics_carry_raw_lagged_mcap_trace(gate_a_result):
    diagnostics = gate_a_result.diagnostics
    assert "total_mcap_lag" in diagnostics.columns
    # No normalized-weight column is ever produced.
    assert not any("weight" in column for column in diagnostics.columns)
    # The second observation for each name carries the first's raw total_mcap.
    aapl = diagnostics.loc[diagnostics["stock_id"] == "AAPL"].reset_index(drop=True)
    assert aapl.loc[1, "total_mcap_lag"] == pytest.approx(
        aapl.loc[0, "total_mcap"]
    )
    assert aapl.loc[1, "lag_source_date"] == aapl.loc[0, "observation_date"]


def test_risk_free_diagnostic_surface_present(gate_a_result):
    rf = gate_a_result.risk_free_diagnostics
    expected = {
        "date",
        "preceding_date",
        "delta_calendar_days",
        "source_date",
        "raw_yield",
        "staleness_business_days",
        "rf",
    }
    assert expected.issubset(set(rf.columns))
    assert pd.isna(rf["rf"].iloc[0])


# ---------------------------------------------------------------------------
# Sanctioned Artifact-A decomposition invariant
# ---------------------------------------------------------------------------
def test_derived_market_return_invariant(gate_a_result):
    factor = gate_a_result.factor.dropna(
        subset=["market_return", "risk_free_return", "MKT"]
    )
    assert not factor.empty
    residual = (
        factor["market_return"] - factor["risk_free_return"] - factor["MKT"]
    ).abs()
    scale = factor["market_return"].abs().clip(lower=1.0)
    assert (residual <= DECOMPOSITION_REL_TOL * scale).all()


# ---------------------------------------------------------------------------
# Structural guard: the orchestration calls the authoritative MKT function
# ---------------------------------------------------------------------------
def test_orchestration_calls_authoritative_market_function(monkeypatch):
    real = capm_pilot.compute_market_excess_return
    calls: list[int] = []

    def spy(*args, **kwargs):
        calls.append(1)
        frame = real(*args, **kwargs)
        sentinel = frame.copy()
        sentinel["MKT"] = 0.125
        return sentinel

    monkeypatch.setattr(capm_pilot, "compute_market_excess_return", spy)
    result = _run()

    assert len(calls) == 1
    non_null = result.factor["MKT"].dropna()
    assert not non_null.empty
    assert np.allclose(non_null.to_numpy(), 0.125)


def test_source_text_has_no_capm_private_symbols():
    """Literal source-grep guard (mirrors P5A-5's DJIA-terminology sweep):
    the module's own source text must contain no reference to any
    ``smart_beta.benchmarks.capm`` private symbol."""
    source = Path(capm_pilot.__file__).read_text(encoding="utf-8")

    for symbol in _CAPM_PRIVATE_SYMBOLS:
        assert symbol not in source, symbol

    match = re.search(
        r"from smart_beta\.benchmarks\.capm import ([^\n]+)", source
    )
    assert match is not None
    imported = [
        part.strip().split(" as ")[0].strip()
        for part in match.group(1).split(",")
    ]
    assert imported == ["compute_market_excess_return"]
    assert all(not name.startswith("_") for name in imported)
    assert "capm._" not in source
    assert "benchmarks.capm as" not in source


# ---------------------------------------------------------------------------
# Live evidence provenance + the real GOOGL plan-tier barrier
# ---------------------------------------------------------------------------
def test_fixture_manifest_records_blocked_gate_a_and_both_findings():
    """The manifest must keep the two findings separate: GATE-A-429 is the
    temporary rate-limit recapture actually on disk; GATE-A-1 is the
    persistent GOOGL entitlement mismatch that blocks Gate A."""
    manifest = _manifest()
    provenance = manifest["_provenance"]
    assert provenance["live_recorded"] is True
    assert provenance["gate_a_window"] == {
        "start": GATE_A_START,
        "end": GATE_A_END,
    }
    assert provenance["candidate_universe"] == list(CANDIDATE_UNIVERSE)
    assert provenance["api_key_stored"] is False
    assert provenance["gate_a_disposition"] == "BLOCKED"
    assert provenance["gate_a_pass"] is False
    # No weakened universe is recorded anywhere.
    assert "entitlement_run_universe" not in provenance

    # Every on-disk recording is the temporary 429 condition, never the 400.
    statuses = {
        int(entry["status_code"])
        for entry in manifest["recordings"].values()
    }
    assert statuses == {429}

    findings = {finding["id"]: finding for finding in provenance["findings"]}
    assert set(findings) == {"GATE-A-429", "GATE-A-1"}
    assert findings["GATE-A-429"]["classification"] == (
        "temporary_operational_rate_limit"
    )
    assert findings["GATE-A-1"]["classification"] == (
        "persistent_entitlement_mismatch"
    )
    assert findings["GATE-A-1"]["http_status"] == 400
    assert findings["GATE-A-1"]["not"] == "transient_rate_limit"


def test_googl_market_cap_is_plan_tier_blocked_offline():
    """The GOOGL daily-fundamentals plan-tier restriction is the persistent
    Gate A blocker. This test is skipped while the committed recording holds
    only the temporary 429 bodies (the real 400 specimen was lost), and is
    retained for the post-unblock re-record."""
    _require_valid_gate_a_fixtures()
    source = TiingoPITSource(["GOOGL"], client=_offline_client())
    with pytest.raises(TiingoAPIError) as excinfo:
        source.get_market_cap(GATE_A_START, GATE_A_END)
    assert "DOW 30" in str(excinfo.value.body)
