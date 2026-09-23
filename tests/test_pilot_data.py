"""Tests for the Pilot-1A P1A-G1 PIT input adapter.

Coverage follows the frozen G1 task spec and test strategy
(``worker_tasks/pilot1/pilot1-plan.md`` sections 10 and 19): the fixture
integrity helper, the provenance record, the frozen semantic input and
capability, the claim boundary (a deliberately over-claiming double must
fail), fail-closed observation handling, the dry-run date cap, the
``marketCap``/fundamentals exclusion, the offline path, and independently
computed adjusted-return specimens.

The suite is offline: the shared ``offline_guard`` fixture blocks
``urlopen``, ``socket.connect`` and ``socket.create_connection`` and scrubs
every data and model credential. Every load replays the committed Gate-B
fixtures through the existing ``replay_transport``; corruption tests always
copy fixtures into ``tmp_path`` and never modify ``tests/fixtures``.
"""

from __future__ import annotations

import dataclasses
import json
import shutil
import subprocess
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from pilot_support import offline_guard  # noqa: F401

from smart_beta.pilot.data import (
    DAILY_TOTAL_RETURN,
    DAILY_TOTAL_RETURN_REQUIREMENT,
    GATE_B_UNIVERSE,
    MAX_EVIDENCE_CLASS,
    ClaimBoundaryError,
    FixtureDigest,
    FixtureFormatError,
    FixtureIntegrityError,
    ObservationError,
    PilotData,
    PilotDataError,
    assert_frozen_claim,
    compute_fixture_hashes,
    load_pit_inputs,
    verify_fixture_hashes,
)
from smart_beta.pit.schema import ADJUSTED_RETURN_COL, DATE_COL, STOCK_COL
from smart_beta.spec.engine import EvidenceClass, VintageEvidence
from smart_beta.spec.requirements import (
    DataRequirement,
    Frequency,
    ObservationPeriod,
    RevisionPolicy,
    Unit,
    find_vendor_names,
)

pytestmark = pytest.mark.usefixtures("offline_guard")

_TESTS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _TESTS_DIR.parent
FIXTURE_DIR = _TESTS_DIR / "fixtures" / "tiingo" / "phase5a_gate_b"
FIXTURE_RELATIVE = "tests/fixtures/tiingo/phase5a_gate_b"
#: The frozen git tree object id of the fixture directory (plan section 5).
FIXTURE_TREE_ID = "84c80f574d90d6cc4567eb5369eb22f450580936"

GATE_B_START = "2025-09-05"
GATE_B_END = "2026-09-15"
DRY_RUN_CAP = "2026-07-01"

_EXPECTED_GATE_B_UNIVERSE = (
    "MMM",
    "AXP",
    "AMGN",
    "AAPL",
    "BA",
    "CAT",
    "CVX",
    "CSCO",
    "KO",
    "DIS",
    "GS",
    "HD",
    "HON",
    "IBM",
    "JNJ",
    "JPM",
    "MCD",
    "MRK",
    "MSFT",
    "NKE",
    "PG",
    "CRM",
    "TRV",
    "UNH",
    "V",
    "WMT",
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _fixture_hashes() -> tuple[FixtureDigest, ...]:
    return compute_fixture_hashes(FIXTURE_DIR)


def _load(**overrides) -> PilotData:
    kwargs = dict(
        fixture_dir=FIXTURE_DIR,
        expected_fixture_hashes=_fixture_hashes(),
        universe=GATE_B_UNIVERSE,
        start=GATE_B_START,
        end=GATE_B_END,
        requirement=DAILY_TOTAL_RETURN_REQUIREMENT,
        fixture_tree_id=FIXTURE_TREE_ID,
    )
    kwargs.update(overrides)
    return load_pit_inputs(**kwargs)


def _copy_fixtures(tmp_path: Path) -> Path:
    destination = tmp_path / "phase5a_gate_b"
    shutil.copytree(FIXTURE_DIR, destination)
    return destination


def _eod_path(fixture_dir: Path, ticker: str = "KO") -> Path:
    return fixture_dir / f"{ticker.lower()}_eod_prices_2025-09-05_2026-09-15.json"


def _read_rows(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_rows(path: Path, rows: list[dict]) -> None:
    path.write_text(json.dumps(rows), encoding="utf-8")


def _hashes_of(fixture_dir: Path):
    return compute_fixture_hashes(fixture_dir)


def _realized_value(data: PilotData, stock_id: str, date_str: str) -> float:
    frame = data.realized_returns
    mask = (frame[STOCK_COL] == stock_id) & (frame[DATE_COL] == pd.Timestamp(date_str))
    rows = frame.loc[mask]
    assert len(rows) == 1, (stock_id, date_str, len(rows))
    return float(rows[ADJUSTED_RETURN_COL].iloc[0])


def _raw_specimen(ticker: str, date_str: str) -> tuple[dict, dict]:
    """Return the ``(previous, current)`` raw EOD rows bracketing ``date_str``."""
    rows = _read_rows(_eod_path(FIXTURE_DIR, ticker))
    dates = [row["date"][:10] for row in rows]
    index = dates.index(date_str)
    assert index > 0, "a return specimen needs a preceding trading row"
    return rows[index - 1], rows[index]


# ---------------------------------------------------------------------------
# fixture integrity: the per-file SHA-256 helper
# ---------------------------------------------------------------------------
def test_compute_fixture_hashes_is_complete_and_well_formed():
    digests = _fixture_hashes()
    assert len(digests) == 79, "78 recordings + manifest.json"
    paths = [digest.path for digest in digests]
    assert paths == sorted(paths)
    assert len(set(paths)) == len(paths)
    assert "manifest.json" in paths
    for digest in digests:
        assert len(digest.sha256) == 64
        int(digest.sha256, 16)


def test_frozen_hash_list_matches_the_frozen_git_tree():
    """The helper's list is anchored to the committed fixture tree object."""
    completed = subprocess.run(
        ["git", "rev-parse", f"HEAD:{FIXTURE_RELATIVE}"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    assert completed.stdout.strip() == FIXTURE_TREE_ID


def test_verify_fixture_hashes_accepts_the_frozen_list():
    digests = verify_fixture_hashes(FIXTURE_DIR, _fixture_hashes())
    assert len(digests) == 79
    # A mapping form and a (path, sha256) sequence form are equivalent.
    as_mapping = {digest.path: digest.sha256 for digest in digests}
    verify_fixture_hashes(FIXTURE_DIR, as_mapping)
    verify_fixture_hashes(
        FIXTURE_DIR, [(digest.path, digest.sha256) for digest in digests]
    )


def test_missing_fixture_file_fails_closed(tmp_path):
    fixture_dir = _copy_fixtures(tmp_path)
    frozen = _fixture_hashes()
    _eod_path(fixture_dir, "KO").unlink()
    with pytest.raises(FixtureIntegrityError, match="missing"):
        _load(fixture_dir=fixture_dir, expected_fixture_hashes=frozen)


def test_extra_fixture_file_fails_closed(tmp_path):
    fixture_dir = _copy_fixtures(tmp_path)
    frozen = _fixture_hashes()
    (fixture_dir / "unexpected_extra.json").write_text("{}", encoding="utf-8")
    with pytest.raises(FixtureIntegrityError, match="extra"):
        _load(fixture_dir=fixture_dir, expected_fixture_hashes=frozen)


def test_modified_fixture_file_fails_before_parsing(tmp_path):
    fixture_dir = _copy_fixtures(tmp_path)
    frozen = _fixture_hashes()
    # Deliberately invalid JSON: if hashing ran first, we get an integrity
    # error, never a JSON parse error.
    _eod_path(fixture_dir, "KO").write_text("{ not json", encoding="utf-8")
    with pytest.raises(FixtureIntegrityError, match="modified"):
        _load(fixture_dir=fixture_dir, expected_fixture_hashes=frozen)


def test_fixture_digest_rejects_malformed_hash():
    with pytest.raises(FixtureIntegrityError):
        FixtureDigest(path="x.json", sha256="not-a-hash")


def test_empty_frozen_list_fails_closed():
    with pytest.raises(FixtureIntegrityError):
        verify_fixture_hashes(FIXTURE_DIR, {})


def test_fixture_directory_must_exist(tmp_path):
    with pytest.raises(FixtureIntegrityError, match="does not exist"):
        compute_fixture_hashes(tmp_path / "nope")


# ---------------------------------------------------------------------------
# provenance / frozen semantic input
# ---------------------------------------------------------------------------
def test_provenance_record_is_complete(gate_b_data):
    provenance = gate_b_data.provenance
    assert provenance.fixture_tree_id == FIXTURE_TREE_ID
    assert len(provenance.file_hashes) == 79
    assert provenance.universe == _EXPECTED_GATE_B_UNIVERSE
    assert provenance.start == GATE_B_START
    assert provenance.end == GATE_B_END
    assert provenance.date_cap is None
    assert provenance.semantic_id == DAILY_TOTAL_RETURN
    assert provenance.evidence_class == EvidenceClass.LIVE_RECORDED.value
    assert provenance.coverage_start == "2025-09-05"
    assert provenance.coverage_end == "2026-09-15"
    assert provenance.observation_count == 257
    assert "trading date" in provenance.knowledge_date_rule
    assert "phase4b_tiingo_certification" in provenance.knowledge_date_rule
    assert provenance.requirement["semantic_id"] == DAILY_TOTAL_RETURN

    payload = provenance.to_dict()
    assert payload["file_hash_count"] == 79
    assert payload["universe_size"] == 26
    assert payload["requirement"]["require_positive_vintage_identity"] is False
    assert isinstance(provenance.content_hash(), str)
    assert len(provenance.content_hash()) == 64


def test_frozen_universe_matches_the_committed_manifest(gate_b_data):
    manifest = json.loads((FIXTURE_DIR / "manifest.json").read_text(encoding="utf-8"))
    assert tuple(GATE_B_UNIVERSE) == _EXPECTED_GATE_B_UNIVERSE
    assert manifest["_provenance"]["frozen_universe"] == list(GATE_B_UNIVERSE)
    assert len(GATE_B_UNIVERSE) == 26


def test_semantic_id_is_exactly_daily_total_return(gate_b_data):
    assert gate_b_data.trusted_input.capability.semantic_id == "daily_total_return"
    assert DAILY_TOTAL_RETURN == "daily_total_return"
    assert DAILY_TOTAL_RETURN_REQUIREMENT.semantic_id == "daily_total_return"


def test_requirement_and_provenance_are_vendor_free():
    requirement_text = json.dumps(
        DAILY_TOTAL_RETURN_REQUIREMENT.to_dict(), sort_keys=True
    )
    assert find_vendor_names(requirement_text) == ()
    # The frozen semantic input itself must stay vendor-free; only the
    # adapter provenance prose may cite the underlying data source.
    assert find_vendor_names(DAILY_TOTAL_RETURN) == ()


# ---------------------------------------------------------------------------
# capability / evidence class
# ---------------------------------------------------------------------------
def test_capability_matches_the_frozen_declaration(gate_b_data):
    capability = gate_b_data.trusted_input.capability
    assert capability.semantic_id == "daily_total_return"
    assert capability.frequency == Frequency.DAILY
    assert capability.observation_period == ObservationPeriod.PERIOD
    assert capability.units == Unit.FRACTION
    assert capability.history == 257
    assert capability.has_knowledge_date is True
    assert capability.has_positive_vintage_identity is False
    assert capability.revision_policies == frozenset({RevisionPolicy.POINT_IN_TIME})


def test_evidence_class_is_live_recorded_and_never_higher(gate_b_data):
    assert gate_b_data.trusted_input.evidence_class == EvidenceClass.LIVE_RECORDED
    assert MAX_EVIDENCE_CLASS == EvidenceClass.LIVE_RECORDED
    assert gate_b_data.trusted_input.vintage_evidence is None
    assert assert_frozen_claim(gate_b_data.trusted_input) is gate_b_data.trusted_input


def test_overclaiming_positive_vintage_double_must_fail(gate_b_data):
    real = gate_b_data.trusted_input
    overclaimed_capability = dataclasses.replace(
        real.capability, has_positive_vintage_identity=True
    )
    double = dataclasses.replace(real, capability=overclaimed_capability)
    with pytest.raises(ClaimBoundaryError, match="positive_vintage_identity"):
        assert_frozen_claim(double)
    # The real adapter output never claims it.
    assert real.capability.has_positive_vintage_identity is False


def test_overclaiming_evidence_class_double_must_fail(gate_b_data):
    real = gate_b_data.trusted_input
    double = dataclasses.replace(
        real, evidence_class=EvidenceClass.OFFICIAL_VENDOR_CERTIFIED
    )
    with pytest.raises(ClaimBoundaryError, match="exceeds the frozen maximum"):
        assert_frozen_claim(double)


def test_overclaiming_vintage_evidence_double_must_fail(gate_b_data):
    real = gate_b_data.trusted_input
    knowledge = pd.DataFrame(
        [[pd.Timestamp("2025-09-08")]], columns=["KO"], index=[pd.Timestamp("2025-09-08")]
    )
    double = dataclasses.replace(
        real, vintage_evidence=VintageEvidence(knowledge_dates=knowledge)
    )
    with pytest.raises(ClaimBoundaryError, match="vintage evidence"):
        assert_frozen_claim(double)


def test_claim_boundary_rejects_a_mismatched_semantic(gate_b_data):
    real = gate_b_data.trusted_input
    double = dataclasses.replace(
        real, capability=dataclasses.replace(real.capability, semantic_id="market_cap")
    )
    with pytest.raises(ClaimBoundaryError):
        assert_frozen_claim(double)


# ---------------------------------------------------------------------------
# realized-return panel / knowledge-date rule
# ---------------------------------------------------------------------------
def test_realized_return_panel_schema(gate_b_data):
    frame = gate_b_data.realized_returns
    assert list(frame.columns) == [DATE_COL, STOCK_COL, ADJUSTED_RETURN_COL]
    assert pd.api.types.is_datetime64_any_dtype(frame[DATE_COL])
    assert pd.api.types.is_float_dtype(frame[ADJUSTED_RETURN_COL])
    assert frame[DATE_COL].is_monotonic_increasing
    assert set(frame[STOCK_COL]) == set(GATE_B_UNIVERSE)
    assert len(frame) == 257 * 26


def test_knowledge_date_is_the_trading_date_for_every_cell(gate_b_data):
    values = gate_b_data.trusted_input.values
    realized_dates = pd.DatetimeIndex(
        sorted(gate_b_data.realized_returns[DATE_COL].unique())
    )
    assert values.index.equals(realized_dates)
    assert isinstance(values.index, pd.DatetimeIndex)
    assert values.index.is_monotonic_increasing
    assert bool(values.notna().to_numpy().all())
    assert gate_b_data.trusted_input.capability.has_knowledge_date is True


def test_value_frame_is_a_wide_date_by_stock_grid(gate_b_data):
    values = gate_b_data.trusted_input.values
    assert values.shape == (257, 26)
    assert list(values.columns) == sorted(GATE_B_UNIVERSE)
    assert values.index.min() == pd.Timestamp("2025-09-08")
    assert values.index.max() == pd.Timestamp("2026-09-15")


def test_loads_are_deterministic():
    first = _load()
    second = _load()
    pd.testing.assert_frame_equal(
        first.realized_returns, second.realized_returns, check_dtype=False
    )
    pd.testing.assert_frame_equal(
        first.trusted_input.values, second.trusted_input.values, check_dtype=False
    )
    assert first.provenance.content_hash() == second.provenance.content_hash()


# ---------------------------------------------------------------------------
# independently computed adjusted-return specimens
# ---------------------------------------------------------------------------
def test_corporate_action_free_adjusted_return_specimen(gate_b_data):
    """AAPL 2025-09-08: no split, no dividend, so adj_ret == raw close return."""
    previous, current = _raw_specimen("AAPL", "2025-09-08")
    assert current["splitFactor"] == 1.0
    assert current["divCash"] == 0.0
    expected = current["close"] / previous["close"] - 1.0
    observed = _realized_value(gate_b_data, "AAPL", "2025-09-08")
    assert observed == pytest.approx(expected, rel=1e-12, abs=1e-15)


def test_dividend_adjusted_return_specimen(gate_b_data):
    """KO 2025-09-15: total return = (close + divCash)/prev_close - 1."""
    previous, current = _raw_specimen("KO", "2025-09-15")
    assert current["divCash"] != 0.0
    assert current["splitFactor"] == 1.0
    expected = (current["close"] + current["divCash"]) / previous["close"] - 1.0
    observed = _realized_value(gate_b_data, "KO", "2025-09-15")
    assert observed == pytest.approx(expected, rel=1e-12, abs=1e-15)
    # Independent of the fixture's own adjusted-close column: the vendor
    # adjClose-based return would differ, proving the raw+action path is used.
    vendor_only = current["adjClose"] / previous["adjClose"] - 1.0
    assert observed != pytest.approx(vendor_only, rel=1e-12, abs=1e-15)


# ---------------------------------------------------------------------------
# fail-closed observation handling
# ---------------------------------------------------------------------------
def test_missing_observation_fails_closed(tmp_path):
    fixture_dir = _copy_fixtures(tmp_path)
    path = _eod_path(fixture_dir, "KO")
    rows = _read_rows(path)
    del rows[50]
    _write_rows(path, rows)
    with pytest.raises(ObservationError, match="missing"):
        _load(
            fixture_dir=fixture_dir,
            expected_fixture_hashes=_hashes_of(fixture_dir),
        )


def test_duplicate_row_fails_closed(tmp_path):
    fixture_dir = _copy_fixtures(tmp_path)
    path = _eod_path(fixture_dir, "KO")
    rows = _read_rows(path)
    rows.insert(51, dict(rows[50]))
    _write_rows(path, rows)
    with pytest.raises(ObservationError, match="duplicate"):
        _load(
            fixture_dir=fixture_dir,
            expected_fixture_hashes=_hashes_of(fixture_dir),
        )


def test_non_finite_value_fails_closed(tmp_path):
    fixture_dir = _copy_fixtures(tmp_path)
    path = _eod_path(fixture_dir, "KO")
    rows = _read_rows(path)
    rows[50]["close"] = float("nan")
    _write_rows(path, rows)
    with pytest.raises(ObservationError, match="non-finite"):
        _load(
            fixture_dir=fixture_dir,
            expected_fixture_hashes=_hashes_of(fixture_dir),
        )


def test_requested_end_outside_coverage_fails_closed():
    with pytest.raises(ObservationError, match="after fixture coverage"):
        _load(end="2026-10-01")


def test_requested_start_outside_coverage_fails_closed():
    with pytest.raises(ObservationError, match="before fixture coverage"):
        _load(start="2025-09-01")


def test_empty_universe_fails_closed():
    with pytest.raises(PilotDataError, match="universe"):
        _load(universe=())


def test_duplicate_universe_fails_closed():
    with pytest.raises(PilotDataError, match="duplicate"):
        _load(universe=("KO", "KO"))


# ---------------------------------------------------------------------------
# non-frozen field exclusion / no market-cap or fundamentals read
# ---------------------------------------------------------------------------
def test_requesting_market_cap_fails_closed():
    market_cap = DataRequirement(
        semantic_id="market_cap",
        frequency=Frequency.DAILY,
        observation_period=ObservationPeriod.INSTANT,
        units=Unit.CURRENCY,
        revision_policy=RevisionPolicy.POINT_IN_TIME,
        require_knowledge_date=True,
    )
    with pytest.raises(PilotDataError, match="daily_total_return"):
        _load(requirement=market_cap)


def test_requesting_any_non_frozen_field_fails_closed():
    for semantic_id in ("peRatio", "pbRatio", "enterpriseVal", "volume"):
        requirement = DataRequirement(
            semantic_id=semantic_id,
            frequency=Frequency.DAILY,
            observation_period=ObservationPeriod.INSTANT,
            revision_policy=RevisionPolicy.POINT_IN_TIME,
            require_knowledge_date=True,
        )
        with pytest.raises(PilotDataError):
            _load(requirement=requirement)


def test_adapter_never_reads_market_cap_or_fundamentals(monkeypatch):
    from smart_beta.vendors.tiingo.source import TiingoPITSource

    def _boom(*args: object, **kwargs: object) -> object:
        raise AssertionError("G1 must never read marketCap/fundamentals")

    monkeypatch.setattr(TiingoPITSource, "get_market_cap", _boom)
    monkeypatch.setattr(TiingoPITSource, "get_fundamentals", _boom)
    data = _load()
    assert not data.realized_returns.empty
    assert "marketCap" not in data.realized_returns.columns


# ---------------------------------------------------------------------------
# dry-run date cap
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def capped_data():
    return _load(date_cap=DRY_RUN_CAP)


def test_date_cap_is_never_exceeded(capped_data, gate_b_data):
    cap = pd.Timestamp(DRY_RUN_CAP)
    assert capped_data.realized_returns[DATE_COL].max() < cap
    assert capped_data.trusted_input.values.index.max() < cap
    assert capped_data.provenance.date_cap == DRY_RUN_CAP
    assert capped_data.provenance.max_observation_date < DRY_RUN_CAP
    assert capped_data.realized_returns[DATE_COL].max() == pd.Timestamp("2026-06-30")


def test_date_cap_only_removes_rows_at_or_after_the_cap(capped_data, gate_b_data):
    cap = pd.Timestamp(DRY_RUN_CAP)
    expected = gate_b_data.realized_returns[
        gate_b_data.realized_returns[DATE_COL] < cap
    ].reset_index(drop=True)
    pd.testing.assert_frame_equal(
        capped_data.realized_returns.reset_index(drop=True),
        expected,
        check_dtype=False,
    )


def test_date_cap_before_coverage_fails_closed():
    with pytest.raises(ObservationError, match="date cap"):
        _load(date_cap="2025-09-01")


# ---------------------------------------------------------------------------
# provenance stability of a capped load
# ---------------------------------------------------------------------------
def test_capped_provenance_records_the_cap(capped_data):
    payload = capped_data.provenance.to_dict()
    assert payload["date_cap"] == DRY_RUN_CAP
    assert payload["coverage_end"] == "2026-09-15"
    assert payload["max_observation_date"] == "2026-06-30"
    assert payload["observation_count"] == 204


# ---------------------------------------------------------------------------
# offline path
# ---------------------------------------------------------------------------
def test_offline_guard_is_active():
    with pytest.raises(RuntimeError, match="offline_guard"):
        urllib.request.urlopen("https://api.tiingo.com")
    with pytest.raises(RuntimeError, match="offline_guard"):
        __import__("socket").create_connection(("api.tiingo.com", 443))


def test_loading_succeeds_under_the_offline_guard(gate_b_data):
    # The module fixture itself replays fixtures; this asserts the live path
    # is never needed and the guard is installed for every test.
    assert np.isfinite(
        gate_b_data.realized_returns[ADJUSTED_RETURN_COL].to_numpy()
    ).all()


# ---------------------------------------------------------------------------
# module-level fixtures
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def gate_b_data():
    return _load()
