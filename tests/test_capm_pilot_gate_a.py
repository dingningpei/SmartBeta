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

Frozen Gate A window: 2026-06-15 .. 2026-09-15. Final universe: AAPL, MSFT,
JPM.

**Gate A is RUN.** Two upstream/configuration findings were resolved on the
way here, both retained as permanent history, never deleted or softened:

* **GATE-A-1** (persistent entitlement mismatch): the originally frozen
  universe (AAPL, MSFT, GOOGL) could not execute -- GOOGL's required
  daily-fundamentals/market-cap endpoint returned a persistent HTTP 400
  plan-tier "DOW 30" restriction. Resolved by a universe amendment
  (GOOGL -> JPM) after a bounded live entitlement probe. The original raw
  HTTP 400 body was subsequently lost and is not reconstructed; GATE-A-1
  remains a *reported*, not a re-certifiable LIVE-RECORDED, finding.
* **GATE-A-2** (spec/configuration defect): running the real AAPL/MSFT/JPM
  fixtures with unmodified ``DEFAULT_SETTINGS`` produced ``universe_count =
  2`` on every date (JPM could never pass the trusted bottom-market-cap
  screen's strict same-set quantile comparison at n=3, regardless of its
  real ~$0.86-0.90T size). Resolved by a Gate-A-only settings override
  (``bottom_mcap_exclude_pct = 0.0``), with no change to any trusted
  production module.
"""

from __future__ import annotations

import dataclasses
import json
import re
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from smart_beta.config.settings import DEFAULT_SETTINGS
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
from smart_beta.research_inputs.tradability import TRADABLE_COL
from smart_beta.vendors.tiingo.client import TiingoClient, replay_transport
from smart_beta.vendors.tiingo.source import TiingoPITSource

# ---------------------------------------------------------------------------
# Frozen Gate A constants
# ---------------------------------------------------------------------------
GATE_A_START = "2026-06-15"
GATE_A_END = "2026-09-15"
GATE_A_UNIVERSE = ("AAPL", "MSFT", "JPM")
EXPECTED_TRADING_DATE_COUNT = 64
EXPECTED_NON_NAN_MKT = 63
DECOMPOSITION_REL_TOL = 1e-9

#: GATE-A-2: the Gate-A-only settings override -- disables only the relative
#: bottom-market-cap screen; all other fields match DEFAULT_SETTINGS exactly.
GATE_A_SETTINGS = dataclasses.replace(DEFAULT_SETTINGS, bottom_mcap_exclude_pct=0.0)

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
_COMPROMISED_DIR = _ARTIFACT_DIR / "gate_a_2_compromised_default_settings"

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


def _body(filename: str):
    return json.loads((_FIXTURE_DIR / filename).read_text(encoding="utf-8"))


def _offline_client() -> TiingoClient:
    """A fixture-fed client: one path -> one recorded response body."""
    recordings: dict[str, tuple[int, object]] = {}
    for filename, entry in _manifest()["recordings"].items():
        recordings[str(entry["url_path"])] = (
            int(entry["status_code"]),
            _body(filename),
        )
    return TiingoClient(transport=replay_transport(recordings))


def _view(client: TiingoClient, tickers=GATE_A_UNIVERSE) -> PointInTimeView:
    return PointInTimeView(TiingoPITSource(list(tickers), client=client))


def _fred_frame() -> pd.DataFrame:
    import io

    body = json.loads(_FRED_FIXTURE.read_text(encoding="utf-8"))
    return pd.read_csv(io.StringIO(body["raw_csv"]))


def _provider(client: TiingoClient, start: str, end: str, tickers=GATE_A_UNIVERSE):
    dates = derive_trading_dates(_view(client, tickers), start, end)
    return TreasuryBillRiskFreeProvider(_fred_frame(), trading_dates=dates)


def _run(
    start: str = GATE_A_START,
    end: str = GATE_A_END,
    tickers=GATE_A_UNIVERSE,
    settings=GATE_A_SETTINGS,
):
    client = _offline_client()
    return run_capm_pilot(
        tickers,
        start,
        end,
        tiingo_client=client,
        risk_free=_provider(client, start, end, tickers),
        settings=settings,
    )


@pytest.fixture(scope="module")
def gate_a_result():
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
            GATE_A_UNIVERSE,
            GATE_A_START,
            GATE_A_END,
            tiingo_client=client,
            risk_free=provider,
            settings=GATE_A_SETTINGS,
        )


# ---------------------------------------------------------------------------
# Artifact A / C schema and consistency
# ---------------------------------------------------------------------------
def test_gate_a_disposition_is_run():
    """The required Gate A artifacts exist as a real result, and the
    machine-readable disposition says so, with both historical findings
    recorded as resolved."""
    disposition = json.loads(
        (_ARTIFACT_DIR / "GATE_A_DISPOSITION.json").read_text(encoding="utf-8")
    )
    assert disposition["disposition"] == "RUN"
    assert disposition["gate_a_pass"] is True
    assert disposition["gate_a_claim"] == "REAL-DATA END-TO-END EXECUTION"
    assert disposition["real_three_name_execution"] == "RUN"
    assert disposition["final_gate_a_universe"] == list(GATE_A_UNIVERSE)
    assert disposition["gate_a_settings"]["bottom_mcap_exclude_pct"] == 0.0
    assert disposition["artifacts"] == {"A": "RUN", "B": "RUN", "C": "RUN"}
    assert disposition["next_task"]["p5a_3"] == "NOT STARTED"

    resolved_ids = {f["id"] for f in disposition["resolved_findings"]}
    assert resolved_ids == {"GATE-A-1", "GATE-A-2"}
    for finding in disposition["resolved_findings"]:
        assert finding["retained_as_history"] is True


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


def test_gate_a_2_compromised_artifacts_are_preserved_and_labeled():
    """The pre-fix (unmodified-DEFAULT_SETTINGS) artifacts are retained
    byte-for-byte but are explicitly labeled NOT Gate A evidence."""
    assert _COMPROMISED_DIR.is_dir()
    for name in (
        "artifact_a_market_factor.json",
        "artifact_b_constituent_diagnostics.json",
        "artifact_b_risk_free_diagnostics.json",
        "artifact_c_statistical_summary.json",
    ):
        assert (_COMPROMISED_DIR / name).is_file(), name
    readme = (_COMPROMISED_DIR / "README.md").read_text(encoding="utf-8")
    assert "NOT certified Gate A output" in readme

    compromised_factor = json.loads(
        (_COMPROMISED_DIR / "artifact_a_market_factor.json").read_text(
            encoding="utf-8"
        )
    )
    # The preserved compromised record genuinely shows the GATE-A-2 defect:
    # universe_count = 2 on every date.
    assert {row["universe_count"] for row in compromised_factor} == {2}


def test_manifest_records_final_universe_all_200_no_googl():
    """The live fixture manifest reflects the final, resolved universe --
    no trace of the original GOOGL entitlement attempt remains in the
    fixture set itself (GATE-A-1's own evidence lives in the disposition/
    provenance docs, not as orphaned fixture files)."""
    manifest = _manifest()
    provenance = manifest["_provenance"]
    assert provenance["live_recorded"] is True
    assert provenance["gate_a_window"] == {
        "start": GATE_A_START,
        "end": GATE_A_END,
    }
    assert provenance["candidate_universe"] == list(GATE_A_UNIVERSE)
    assert provenance["api_key_stored"] is False
    assert provenance["gate_a_disposition"] == "RUN"

    statuses = {
        int(entry["status_code"]) for entry in manifest["recordings"].values()
    }
    assert statuses == {200}

    for filename in manifest["recordings"]:
        assert "googl" not in filename.lower()
    assert not any(_FIXTURE_DIR.glob("googl_*"))


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


def test_universe_count_is_three_on_every_date_after_gate_a_2_fix(gate_a_result):
    """With the Gate-A-only settings override, universe_count is 3 (an
    upper bound from get_tradability, not a reproduction of downstream
    exclusions) on every date -- the GATE-A-2 defect (a guaranteed 2) is
    gone."""
    counts = gate_a_result.factor["universe_count"]
    assert (counts == len(GATE_A_UNIVERSE)).all()


def test_gate_a_2_regression_default_settings_reproduces_the_defect():
    """Regression proof, from the exact same real live-recorded fixtures:
    unmodified DEFAULT_SETTINGS reproduces the GATE-A-2 exclusion (JPM
    excluded on every date), while the frozen Gate-A-only settings
    override removes it. This is a real-data regression check tied to
    this specific Gate A dataset, not a new test of production tradability
    policy semantics in the abstract."""
    assert DEFAULT_SETTINGS.bottom_mcap_exclude_pct == pytest.approx(0.30)

    compromised = _run(settings=DEFAULT_SETTINGS)
    assert (compromised.factor["universe_count"] == 2).all()

    jpm_rows = compromised.diagnostics.loc[
        compromised.diagnostics["stock_id"] == "JPM"
    ]
    assert not jpm_rows.empty
    assert (jpm_rows[TRADABLE_COL] == False).all()  # noqa: E712
    assert set(jpm_rows["exclusion_reason"].dropna()) == {"tradability_policy"}

    fixed = _run(settings=GATE_A_SETTINGS)
    assert (fixed.factor["universe_count"] == 3).all()
    fixed_jpm_rows = fixed.diagnostics.loc[fixed.diagnostics["stock_id"] == "JPM"]
    assert (fixed_jpm_rows[TRADABLE_COL] == True).all()  # noqa: E712


def test_recorder_script_uses_gate_a_2_settings_override():
    """Literal source-grep guard: the live recorder's own committed source
    must construct and pass the GATE-A-2 settings override, not silently
    fall back to a bare DEFAULT_SETTINGS call."""
    script_path = (
        Path(__file__).resolve().parent.parent
        / "scripts"
        / "fetch_phase5a_gate_a_fixtures.py"
    )
    source = script_path.read_text(encoding="utf-8")
    assert "bottom_mcap_exclude_pct=0.0" in source
    assert re.search(r"run_capm_pilot\([^)]*settings=", source, re.DOTALL)
    assert not re.search(
        r"run_capm_pilot\([^)]*settings=DEFAULT_SETTINGS[^_]", source, re.DOTALL
    )


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
    required Artifact-C summary is exercised directly."""
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
        settings=GATE_A_SETTINGS,
    )
    assert result.statistics["n"] < MINIMUM_OBSERVATIONS
    assert result.statistics["status"] == INSUFFICIENT_OBSERVATIONS_STATUS
    assert result.statistics["mean"] is None
    assert result.statistics["std"] is None
    assert result.statistics["newey_west_tstat"] is None


# ---------------------------------------------------------------------------
# Artifact B: named exclusions and diagnostics (CONSTRUCTED, per the frozen
# spec's "if none does, construct one labeled CONSTRUCTED" fallback -- the
# real, GATE-A-2-fixed Gate A run has zero real exclusions to name).
# ---------------------------------------------------------------------------
def test_excluded_observation_is_named_with_reason_constructed():
    """CONSTRUCTED (not live evidence): the real, corrected Gate A run has
    no real tradability exclusion to name (all three names are tradable on
    every date -- see test_universe_count_is_three_on_every_date_after_
    gate_a_2_fix). The frozen spec requires this exclusion-naming path be
    exercised anyway; it is proven here against a small, hand-built
    tradability frame using the same production helper."""
    tradability = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-06-16", "2026-06-16"]),
            "stock_id": ["AAPL", "MSFT"],
            TRADABLE_COL: [True, False],
            "delisting_uncertain": [False, False],
        }
    )
    reasons = capm_pilot._exclusion_reasons(tradability)
    assert pd.isna(reasons.iloc[0])
    assert reasons.iloc[1] == "tradability_policy"


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
    """Literal source-grep guard (mirrors P5A-5's DJIA-terminology sweep
    pattern): the module's own source text must contain no reference to
    any ``smart_beta.benchmarks.capm`` private symbol."""
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
