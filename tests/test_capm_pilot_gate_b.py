"""Gate B fixed-universe scalability tests (Phase 5A, P5A-4).

Everything here is offline: the real, live-recorded Tiingo fixtures under
``tests/fixtures/tiingo/phase5a_gate_b/`` and P5A-4's own FRED fixture under
``tests/fixtures/risk_free/treasury_gate_b/`` are replayed through the
existing, unmodified ``replay_transport``. An autouse tripwire replaces
``urllib.request.urlopen`` so an accidental live call fails loudly (the
zero-live-network requirement is therefore structural, not a single test).

The module under test is P5A-2's ``run_capm_pilot``, **reused unmodified**;
this task adds no production code. These tests prove the Gate B run is
schema-conformant, that the frozen universe in the committed provenance
record is exactly what was run, that every entitlement exclusion is named
with its reason, and that the sanctioned Artifact-A decomposition and the
RF date-grid invariants still hold at ~30 names / ~12 months.

Frozen Gate B window: 2025-09-15 .. 2026-09-15. The universe is "a fixed
universe selected from a dated DJIA constituent snapshot" (Wikipedia's
"List of Dow Jones Industrial Average companies", revision 1369516696,
2026-08-15). It is held constant for the whole window.

Gate B claim boundary (frozen): "BOUNDED FIXED-UNIVERSE SCALABILITY" --
this does not demonstrate representative US market coverage,
survivorship-safe historical membership, publication-grade CAPM
replication, or investment performance.
"""

from __future__ import annotations

import json
import re
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from smart_beta.config.settings import DEFAULT_SETTINGS
from smart_beta.pipelines.capm_pilot import (
    MINIMUM_OBSERVATIONS,
    derive_trading_dates,
    run_capm_pilot,
)
from smart_beta.pit.view import PointInTimeView
from smart_beta.research_inputs.risk_free_treasury import (
    TreasuryBillRiskFreeProvider,
)
from smart_beta.research_inputs.tradability import TRADABLE_COL
from smart_beta.vendors.tiingo.client import TiingoClient, replay_transport
from smart_beta.vendors.tiingo.source import TiingoPITSource

# ---------------------------------------------------------------------------
# Frozen Gate B constants
# ---------------------------------------------------------------------------
GATE_B_START = "2025-09-15"
GATE_B_END = "2026-09-15"
DECOMPOSITION_REL_TOL = 1e-9

#: The full publicly documented, dated DJIA constituent snapshot candidates.
GATE_B_CANDIDATES = (
    "MMM", "GOOGL", "AXP", "AMGN", "AMZN", "AAPL", "BA", "CAT", "CVX",
    "CSCO", "KO", "DIS", "GS", "HD", "HON", "IBM", "JNJ", "JPM", "MCD",
    "MRK", "MSFT", "NKE", "NVDA", "PG", "CRM", "SHW", "TRV", "UNH", "V",
    "WMT",
)

#: The four candidates whose required ``total_mcap`` endpoint returns a real
#: persistent HTTP 400 plan-tier "DOW 30" restriction under this Tiingo plan.
EXPECTED_EXCLUDED = ("GOOGL", "AMZN", "NVDA", "SHW")

EXPECTED_FROZEN_UNIVERSE = tuple(
    t for t in GATE_B_CANDIDATES if t not in EXPECTED_EXCLUDED
)

_TESTS_DIR = Path(__file__).resolve().parent
_FIXTURE_DIR = _TESTS_DIR / "fixtures" / "tiingo" / "phase5a_gate_b"
_FRED_FIXTURE = (
    _TESTS_DIR
    / "fixtures"
    / "risk_free"
    / "treasury_gate_b"
    / f"dgs3mo_{GATE_B_START}_{GATE_B_END}.json"
)
_ARTIFACT_DIR = _TESTS_DIR.parent / "docs" / "phase5a" / "gate_b"
_UNIVERSE_SNAPSHOT = _ARTIFACT_DIR / "universe_snapshot.json"
_DISPOSITION = _ARTIFACT_DIR / "GATE_B_DISPOSITION.json"
_RECORDER = (
    _TESTS_DIR.parent / "scripts" / "fetch_phase5a_gate_b_fixtures.py"
)


def _forbidden_phrases() -> list[str]:
    """The plan's forbidden universe descriptions, assembled from fragments
    so this test module's own source never literally contains one."""
    return [
        "the " + "DJIA",
        "a historical " + "DJIA universe",
        "a representative US " + "market portfolio",
        "a survivorship-safe " + "index universe",
    ]


# ---------------------------------------------------------------------------
# Fixture / replay helpers
# ---------------------------------------------------------------------------
def _manifest() -> dict:
    return json.loads((_FIXTURE_DIR / "manifest.json").read_text(encoding="utf-8"))


def _body(filename: str):
    return json.loads((_FIXTURE_DIR / filename).read_text(encoding="utf-8"))


def _offline_client() -> TiingoClient:
    recordings: dict[str, tuple[int, object]] = {}
    for filename, entry in _manifest()["recordings"].items():
        recordings[str(entry["url_path"])] = (
            int(entry["status_code"]),
            _body(filename),
        )
    return TiingoClient(transport=replay_transport(recordings))


def _view(client: TiingoClient, tickers=EXPECTED_FROZEN_UNIVERSE) -> PointInTimeView:
    return PointInTimeView(TiingoPITSource(list(tickers), client=client))


def _fred_frame() -> pd.DataFrame:
    import io

    body = json.loads(_FRED_FIXTURE.read_text(encoding="utf-8"))
    return pd.read_csv(io.StringIO(body["raw_csv"]))


def _provider(client: TiingoClient, start: str, end: str):
    dates = derive_trading_dates(_view(client), start, end)
    return TreasuryBillRiskFreeProvider(_fred_frame(), trading_dates=dates)


def _run(settings=DEFAULT_SETTINGS):
    client = _offline_client()
    return run_capm_pilot(
        EXPECTED_FROZEN_UNIVERSE,
        GATE_B_START,
        GATE_B_END,
        tiingo_client=client,
        risk_free=_provider(client, GATE_B_START, GATE_B_END),
        settings=settings,
    )


@pytest.fixture(scope="module")
def gate_b_result():
    return _run()


@pytest.fixture(autouse=True)
def _forbid_live_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*args: object, **kwargs: object) -> object:
        raise AssertionError(
            "Test attempted a live network call; inject replay_transport instead."
        )

    monkeypatch.setattr(urllib.request, "urlopen", _boom)


# ---------------------------------------------------------------------------
# Universe freeze: provenance record is what was actually run
# ---------------------------------------------------------------------------
def test_universe_snapshot_candidate_universe_is_the_frozen_djia_snapshot():
    snapshot = json.loads(_UNIVERSE_SNAPSHOT.read_text(encoding="utf-8"))
    recorded = [
        item["ticker"]
        for item in snapshot["djia_constituent_snapshot"]["candidates"]
    ]
    assert recorded == list(GATE_B_CANDIDATES)
    assert snapshot["djia_constituent_snapshot"]["snapshot_date"] == "2026-09-19"
    assert snapshot["djia_constituent_snapshot"]["revision_id"] == 1369516696
    assert snapshot["universe_terminology"] == (
        "a fixed universe selected from a dated DJIA constituent snapshot"
    )


def test_committed_universe_matches_provenance_record_no_drift():
    """The list that was actually run equals the frozen list in the committed
    provenance record and in every other machine-readable evidence surface."""
    snapshot = json.loads(_UNIVERSE_SNAPSHOT.read_text(encoding="utf-8"))
    disposition = json.loads(_DISPOSITION.read_text(encoding="utf-8"))
    manifest = _manifest()

    assert snapshot["frozen_universe"] == list(EXPECTED_FROZEN_UNIVERSE)
    assert snapshot["frozen_universe_count"] == len(EXPECTED_FROZEN_UNIVERSE)
    assert disposition["frozen_universe"] == list(EXPECTED_FROZEN_UNIVERSE)
    assert manifest["_provenance"]["frozen_universe"] == list(
        EXPECTED_FROZEN_UNIVERSE
    )
    assert disposition["candidate_universe"] == list(GATE_B_CANDIDATES)
    assert manifest["_provenance"]["candidate_universe"] == list(
        GATE_B_CANDIDATES
    )

    # The recorder's own hard-coded candidate tuple must match the snapshot.
    source = _RECORDER.read_text(encoding="utf-8")
    for ticker in GATE_B_CANDIDATES:
        assert f'"{ticker}"' in source


def test_every_entitlement_exclusion_is_named_with_reason():
    snapshot = json.loads(_UNIVERSE_SNAPSHOT.read_text(encoding="utf-8"))
    disposition = json.loads(_DISPOSITION.read_text(encoding="utf-8"))

    excluded = {entry["ticker"]: entry for entry in snapshot["exclusions"]}
    assert set(excluded) == set(EXPECTED_EXCLUDED)
    for ticker, entry in excluded.items():
        assert entry["status_code"] == 400
        assert "DOW 30" in entry["reason"]
        assert ticker not in snapshot["frozen_universe"]

    disposition_excluded = {
        entry["ticker"]: entry for entry in disposition["excluded_names"]
    }
    assert set(disposition_excluded) == set(EXPECTED_EXCLUDED)
    for ticker, entry in disposition_excluded.items():
        assert "DOW 30" in entry["reason"]

    manifest_excluded = {
        entry["ticker"]: entry
        for entry in _manifest()["_provenance"]["excluded_names"]
    }
    assert set(manifest_excluded) == set(EXPECTED_EXCLUDED)
    for ticker, entry in manifest_excluded.items():
        assert "DOW 30" in entry["reason"]


def test_every_candidate_has_a_recorded_probe_outcome():
    snapshot = json.loads(_UNIVERSE_SNAPSHOT.read_text(encoding="utf-8"))
    results = snapshot["entitlement_probe"]["results"]
    assert [item["ticker"] for item in results] == list(GATE_B_CANDIDATES)
    assert snapshot["entitlement_probe"]["probe_date"] == "2026-06-15"
    assert all(
        item["status_code"] in (200, 400) for item in results
    ), "a transient rate-limit status must never be frozen as a probe result"


# ---------------------------------------------------------------------------
# Manifest / FRED provenance
# ---------------------------------------------------------------------------
def test_tiingo_manifest_records_only_200s_for_frozen_universe():
    manifest = _manifest()
    provenance = manifest["_provenance"]
    assert provenance["live_recorded"] is True
    assert provenance["gate_b_window"] == {
        "start": GATE_B_START,
        "end": GATE_B_END,
    }
    assert provenance["api_key_stored"] is False
    assert provenance["gate_b_disposition"] == "RUN"
    statuses = {
        int(entry["status_code"]) for entry in manifest["recordings"].values()
    }
    assert statuses == {200}
    # No fixture file belongs to an excluded name.
    recorded_paths = {
        str(entry["url_path"]) for entry in manifest["recordings"].values()
    }
    for ticker in EXPECTED_EXCLUDED:
        assert not any(f"/{ticker}" in path for path in recorded_paths)


def test_fred_fixture_covers_the_gate_b_window():
    manifest = json.loads(
        (
            _TESTS_DIR
            / "fixtures"
            / "risk_free"
            / "treasury_gate_b"
            / "manifest.json"
        ).read_text(encoding="utf-8")
    )
    provenance = manifest["_provenance"]
    assert provenance["live_recorded"] is True
    assert provenance["series_id"] == "DGS3MO"
    assert provenance["date_range"] == {
        "start": GATE_B_START,
        "end": GATE_B_END,
    }
    assert _FRED_FIXTURE.is_file()


# ---------------------------------------------------------------------------
# Run schema / consistency
# ---------------------------------------------------------------------------
def test_factor_schema_conforms_and_first_row_is_nan(gate_b_result):
    factor = gate_b_result.factor
    assert list(factor.columns) == [
        "date",
        "universe_count",
        "market_return",
        "risk_free_return",
        "MKT",
    ]
    assert factor["date"].is_monotonic_increasing
    assert pd.api.types.is_integer_dtype(factor["universe_count"])
    for column in ("market_return", "risk_free_return", "MKT"):
        assert pd.api.types.is_float_dtype(factor[column])
    assert pd.isna(factor["MKT"].iloc[0])
    assert pd.isna(factor["risk_free_return"].iloc[0])
    assert pd.DatetimeIndex(gate_b_result.trading_dates).equals(
        pd.DatetimeIndex(factor["date"])
    )
    # A ~12-month window on the NYSE grid is far more than the frozen
    # minimum; assert the run is statistically reportable at this scale.
    assert factor["MKT"].notna().sum() >= MINIMUM_OBSERVATIONS


def test_universe_count_is_an_upper_bound_within_frozen_universe(gate_b_result):
    frozen_size = len(EXPECTED_FROZEN_UNIVERSE)
    counts = gate_b_result.factor["universe_count"]
    # DEFAULT_SETTINGS' bottom-mcap screen excludes the smallest names, so the
    # count is strictly less than the candidate set but positive when a
    # cross-section exists.
    assert (counts > 0).all()
    assert (counts < frozen_size).all()
    # The count is an upper bound on actually-weighted names.
    assert (counts <= frozen_size).all()

    # And it is exactly the tradability-policy survivor count per date.
    tradable = gate_b_result.diagnostics.loc[
        gate_b_result.diagnostics[TRADABLE_COL]
    ]
    expected = tradable.groupby("date").size()
    factor = gate_b_result.factor.set_index("date")
    for date_value, count in expected.items():
        assert int(factor.loc[date_value, "universe_count"]) == int(count)


def test_gate_b_uses_default_settings_unmodified():
    """The frozen rule: Gate B keeps DEFAULT_SETTINGS unmodified; the
    Gate-A-only bottom-mcap override is never applied here."""
    assert DEFAULT_SETTINGS.bottom_mcap_exclude_pct == pytest.approx(0.30)
    source = _RECORDER.read_text(encoding="utf-8")
    assert re.search(r"settings=DEFAULT_SETTINGS", source)
    assert "bottom_mcap_exclude_pct" not in source
    assert "dataclasses.replace" not in source


def test_risk_free_grid_matches_authoritative_grid(gate_b_result):
    assert (
        pd.DatetimeIndex(gate_b_result.risk_free_diagnostics["date"])
        .sort_values()
        .equals(pd.DatetimeIndex(gate_b_result.trading_dates).sort_values())
    )
    # The real FRED lookup was exercised across the whole 12-month window.
    rf = gate_b_result.risk_free_diagnostics
    assert rf["source_date"].notna().sum() == len(rf) - 1
    assert rf["delta_calendar_days"].dropna().gt(0).all()


def test_statistical_summary_follows_frozen_convention(gate_b_result):
    summary = gate_b_result.statistics
    assert summary["status"] == "RUN"
    assert summary["n"] == int(gate_b_result.factor["MKT"].notna().sum())
    assert summary["n"] >= MINIMUM_OBSERVATIONS
    assert summary["newey_west_lags"] == 6
    assert np.isfinite(summary["newey_west_tstat"])
    assert "optimality" in summary["lag_optimality_caveat"]


def test_derived_market_return_invariant(gate_b_result):
    factor = gate_b_result.factor.dropna(
        subset=["market_return", "risk_free_return", "MKT"]
    )
    assert not factor.empty
    residual = (
        factor["market_return"] - factor["risk_free_return"] - factor["MKT"]
    ).abs()
    scale = factor["market_return"].abs().clip(lower=1.0)
    assert (residual <= DECOMPOSITION_REL_TOL * scale).all()


def test_diagnostics_carry_raw_lagged_mcap_and_no_normalized_weight(gate_b_result):
    diagnostics = gate_b_result.diagnostics
    assert "total_mcap_lag" in diagnostics.columns
    assert not any("weight" in column for column in diagnostics.columns)
    assert set(diagnostics["stock_id"].unique()) == set(
        EXPECTED_FROZEN_UNIVERSE
    )


# ---------------------------------------------------------------------------
# Committed artifacts
# ---------------------------------------------------------------------------
def test_committed_artifacts_exist_and_are_schema_conformant():
    expected = (
        "artifact_a_market_factor.csv",
        "artifact_a_market_factor.json",
        "artifact_b_constituent_diagnostics.csv",
        "artifact_b_constituent_diagnostics.json",
        "artifact_b_risk_free_diagnostics.csv",
        "artifact_b_risk_free_diagnostics.json",
        "artifact_c_statistical_summary.json",
        "GATE_B_DISPOSITION.json",
        "universe_snapshot.json",
    )
    for name in expected:
        assert (_ARTIFACT_DIR / name).is_file(), name

    factor = pd.DataFrame(
        json.loads(
            (_ARTIFACT_DIR / "artifact_a_market_factor.json").read_text(
                encoding="utf-8"
            )
        )
    )
    assert list(factor.columns) == [
        "date",
        "universe_count",
        "market_return",
        "risk_free_return",
        "MKT",
    ]


def test_disposition_matches_run(gate_b_result):
    disposition = json.loads(_DISPOSITION.read_text(encoding="utf-8"))
    assert disposition["disposition"] == "RUN"
    assert disposition["gate_b_pass"] is True
    assert disposition["gate_b_claim"] == "BOUNDED FIXED-UNIVERSE SCALABILITY"
    assert disposition["settings"] == "DEFAULT_SETTINGS (unmodified)"
    assert disposition["artifacts"] == {"A": "RUN", "B": "RUN", "C": "RUN"}
    assert disposition["trading_date_count"] == len(
        gate_b_result.trading_dates
    )
    assert disposition["non_nan_mkt_observations"] == int(
        gate_b_result.factor["MKT"].notna().sum()
    )
    assert disposition["next_task"]["p5a_5"] == "NOT STARTED"


# ---------------------------------------------------------------------------
# Terminology guard
# ---------------------------------------------------------------------------
def test_required_terminology_present_and_forbidden_absent():
    required = "a fixed universe selected from a dated DJIA constituent snapshot"
    scanned = [
        _RECORDER,
        _UNIVERSE_SNAPSHOT,
        _DISPOSITION,
        _ARTIFACT_DIR / "artifact_c_statistical_summary.json",
    ]
    for path in scanned:
        text = path.read_text(encoding="utf-8")
        for phrase in _forbidden_phrases():
            assert phrase not in text, f"{phrase!r} in {path.name}"
    snapshot_text = _UNIVERSE_SNAPSHOT.read_text(encoding="utf-8")
    assert required in snapshot_text
    assert required in _DISPOSITION.read_text(encoding="utf-8")
    assert required in _RECORDER.read_text(encoding="utf-8")
