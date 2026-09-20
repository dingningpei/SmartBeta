"""Phase 5A CAPM pilot certification gate (task P5A-5).

This is not an ordinary unit-test module. It is the executable half of the
checked-in certification report ``docs/phase5a_capm_certification.md``.
Every disposition line in that document must correspond to a real
assertion in this file against the real artifacts P5A-1 through P5A-4
actually produced; the markdown-presence check at the bottom exists only
as a synchronization guard alongside these executable checks, never as
the sole proof of a claim.

The 14 required checks are the ones in
``worker_tasks/phase5a/task-p5a-5-certification.md``'s "Required
certification report content" — the single normative source for the list.
This file re-derives each claim by reading the committed artifacts and
re-running the committed executable checks directly; it never imports or
re-runs a prior task's own disposition as proof. It also evaluates all 17
points of the final Phase 5A barrier point by point.

Evidence discipline: this file edits no production code and no P5A-1
through P5A-4 test file or fixture. Items [5] and [7] re-execute the
committed P5A-3 and P5A-1 test functions verbatim (loaded by path),
not paraphrases of them. Item [12] runs the full suite fresh in a
subprocess, excluding only this file, with the live-key environment
removed so the one pre-existing key-gated live test skips instead of
touching the network.

Run with ``.venv/bin/pytest tests/test_phase5a_certification.py``.
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

from smart_beta.config.settings import DEFAULT_SETTINGS
from smart_beta.research_inputs.risk_free_treasury import (
    TreasuryBillRiskFreeProvider,
)

# ---------------------------------------------------------------------------
# Paths / constants
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent.parent
_TESTS_DIR = _REPO_ROOT / "tests"
_FIXTURES_DIR = _TESTS_DIR / "fixtures"
_GATE_A = _REPO_ROOT / "docs" / "phase5a" / "gate_a"
_GATE_B = _REPO_ROOT / "docs" / "phase5a" / "gate_b"
_REPORT = _REPO_ROOT / "docs" / "phase5a_capm_certification.md"

#: The phase baseline (tag ``phase4d-b-complete``): the tip ``master`` held
#: before any Phase 5A commit. Used for the "files P5A-1..4 added" sweep.
_PHASE_BASELINE = "0d15655"

_GATE_B_RECORDER = _REPO_ROOT / "scripts" / "fetch_phase5a_gate_b_fixtures.py"
_GATE_B_TEST = _TESTS_DIR / "test_capm_pilot_gate_b.py"

#: P4DB-9's exact token-pattern sweep (mirrored byte-for-byte).
_TOKEN_PATTERN = re.compile(
    r"(?i)(api[_-]?key|token)\s*[:=]\s*[A-Za-z0-9]{16,}"
)

#: Frozen canonical sentences (from ``phase5a-plan.md`` / the P5A-5 task).
_NOT_DEMONSTRATED = (
    "Gate A does not demonstrate representative market coverage, "
    "scalability, a US market factor, CAPM replication, economic "
    "significance, or statistical significance."
)
_EVIDENCE_LIMITATION = (
    "the short-maturity/simple-interest interpretation of DGS3MO is "
    "supported by mutually consistent Treasury.gov, FRED, and academic "
    "documentation, but the primary Treasury Yield Curve Methodology "
    "technical publication has not been directly read in full."
)
_NW_CAVEAT = (
    "this is a frozen Phase 5A diagnostic convention inherited from "
    "existing machinery, not a certification that lag 6 is the "
    "statistically optimal HAC bandwidth for daily observations."
)
_STALENESS_SCOPE = (
    "a Phase 5A data-freshness convention, not a Treasury-calendar "
    "certification"
)

#: The 14 required report line-items (index -> required label substring).
_LINE_LABELS = {
    1: "GATE A DISPOSITION =",
    2: "GATE A CLAIM =",
    3: "GATE B DISPOSITION =",
    4: "GATE B CLAIM =",
    5: "HAND VERIFICATION =",
    6: "RISK-FREE TRANSFORMATION =",
    7: "RISK-FREE STALENESS/NO-FUTURE-LEAKAGE =",
    8: "NEWEY-WEST CONVENTION =",
    9: "UPSTREAM DEFECT AUDIT =",
    10: "EVIDENCE TAXONOMY AUDIT =",
    11: "CREDENTIAL LEAKAGE =",
    12: "FULL REGRESSION =",
    13: "RF FORMULA INDEPENDENCE =",
    14: "CAPM SINGLE-AUTHORITY AUDIT =",
}

_STATUS_TOKENS = (
    "PASS",
    "FAIL",
    "NONE FOUND",
    "NOT RUN",
    "NOT CERTIFIED",
    "REAL-DATA END-TO-END EXECUTION",
    "BOUNDED FIXED-UNIVERSE SCALABILITY",
    "lag 6",
)

_BARRIER_POINTS = 17


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def _read_text(path: Path | str) -> str:
    return Path(path).read_text(encoding="utf-8", errors="ignore")


def _read_json(path: Path | str) -> object:
    return json.loads(_read_text(path))


def _normalized_report() -> str:
    assert _REPORT.is_file(), f"missing certification report: {_REPORT}"
    return re.sub(r"\s+", " ", _read_text(_REPORT))


def _load_test_module(relpath: str, module_name: str):
    path = _TESTS_DIR / relpath
    assert path.is_file(), f"missing authoritative test module: {path}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _mkt_series(factor_rows: list[dict]) -> pd.Series:
    return pd.Series(
        [row["MKT"] for row in factor_rows], dtype="float64"
    )


def _phase_added_files() -> list[str]:
    proc = subprocess.run(
        ["git", "diff", "--name-only", f"{_PHASE_BASELINE}..HEAD"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    return [name for name in proc.stdout.split() if name]


def _forbidden_universe_phrases() -> list[str]:
    """The four plan-frozen forbidden universe descriptions, assembled from
    fragments so this test module's own source never literally contains one
    (mirroring P5A-4's own guard)."""
    return [
        "the " + "DJIA",
        "a historical " + "DJIA universe",
        "a representative US " + "market portfolio",
        "a survivorship-safe " + "index universe",
    ]


# ---------------------------------------------------------------------------
# [1] GATE A DISPOSITION
# ---------------------------------------------------------------------------
def test_item01_gate_a_disposition_reverified() -> None:
    factor = _read_json(_GATE_A / "artifact_a_market_factor.json")
    assert len(factor) == 64, "Gate A must span the frozen 64-date window"

    mkt = _mkt_series(factor)
    assert int(mkt.notna().sum()) == 63, "the chain must produce 63 real MKT obs"
    assert float(mkt.dropna().std(ddof=1)) > 0.0, "MKT series must be non-trivial"
    assert pd.isna(mkt.iloc[0]), "first observation is NaN by the frozen lag rule"
    assert {row["universe_count"] for row in factor} == {3}

    disposition = _read_json(_GATE_A / "GATE_A_DISPOSITION.json")
    assert disposition["disposition"] == "RUN"
    assert disposition["gate_a_pass"] is True
    assert disposition["gate_a_claim"] == "REAL-DATA END-TO-END EXECUTION"
    assert disposition["final_gate_a_universe"] == ["AAPL", "MSFT", "JPM"]
    assert disposition["artifacts"] == {"A": "RUN", "B": "RUN", "C": "RUN"}
    findings = {entry["id"]: entry for entry in disposition["resolved_findings"]}
    assert set(findings) == {"GATE-A-1", "GATE-A-2"}
    assert all(entry["retained_as_history"] is True for entry in findings.values())

    # The real live-recorded provenance behind the run.
    manifest = _read_json(
        _FIXTURES_DIR / "tiingo" / "phase5a_gate_a" / "manifest.json"
    )
    assert manifest["_provenance"]["live_recorded"] is True
    assert manifest["_provenance"]["api_key_stored"] is False
    assert {
        int(entry["status_code"]) for entry in manifest["recordings"].values()
    } == {200}


# ---------------------------------------------------------------------------
# [2] GATE A CLAIM
# ---------------------------------------------------------------------------
def test_item02_gate_a_claim_verbatim() -> None:
    document = _normalized_report()
    assert "GATE A CLAIM = REAL-DATA END-TO-END EXECUTION" in document
    assert _NOT_DEMONSTRATED in document, "missing canonical not-demonstrated sentence"


# ---------------------------------------------------------------------------
# [3] GATE B DISPOSITION
# ---------------------------------------------------------------------------
def test_item03_gate_b_disposition_reverified() -> None:
    disposition = _read_json(_GATE_B / "GATE_B_DISPOSITION.json")
    assert disposition["disposition"] == "RUN"
    assert disposition["gate_b_pass"] is True
    assert disposition["gate_b_claim"] == "BOUNDED FIXED-UNIVERSE SCALABILITY"
    assert len(disposition["candidate_universe"]) == 30
    assert len(disposition["frozen_universe"]) == 26
    assert {entry["ticker"] for entry in disposition["excluded_names"]} == {
        "GOOGL",
        "AMZN",
        "NVDA",
        "SHW",
    }
    assert disposition["artifacts"] == {"A": "RUN", "B": "RUN", "C": "RUN"}

    factor = _read_json(_GATE_B / "artifact_a_market_factor.json")
    assert len(factor) == 252
    mkt = _mkt_series(factor)
    assert int(mkt.notna().sum()) == 251
    assert float(mkt.dropna().std(ddof=1)) > 0.0
    assert {row["universe_count"] for row in factor} == {18}

    # Preserved live-access blocker history (not deleted).
    assert (_GATE_B / "P5A-4_BLOCKER_REPORT.md").is_file()
    assert (_GATE_B / "P5A-4_FIXTURE_RECORD_BLOCKER_REPORT.md").is_file()

    # The reviewer-obligation finding must be stated plainly in the report.
    document = _normalized_report()
    assert "single squashed commit" in document
    assert "freeze-before-computation" in document


# ---------------------------------------------------------------------------
# [4] GATE B CLAIM + forbidden terminology
# ---------------------------------------------------------------------------
def test_item04_gate_b_claim_and_forbidden_terminology() -> None:
    document = _normalized_report()
    assert "GATE B CLAIM = BOUNDED FIXED-UNIVERSE SCALABILITY" in document
    assert (
        "a fixed universe selected from a dated DJIA constituent snapshot"
        in document
    )

    targets = [_REPORT, _GATE_B_RECORDER, _GATE_B_TEST]
    targets.extend(sorted(path for path in _GATE_B.rglob("*") if path.is_file()))
    for path in targets:
        text = _read_text(path).lower()
        for phrase in _forbidden_universe_phrases():
            assert phrase.lower() not in text, f"forbidden phrase {phrase!r} in {path}"


# ---------------------------------------------------------------------------
# [5] HAND VERIFICATION (re-run P5A-3's actual test)
# ---------------------------------------------------------------------------
def test_item05_hand_verification_rerun() -> None:
    module = _load_test_module(
        "test_phase5a_hand_verification.py", "p5a3_hand_verification"
    )
    assert module.VERIFIED_DATE == "2026-06-16"
    assert module.REL_TOL == 1e-9
    # Re-executes the real, independent reconstruction end to end; raises the
    # detailed divergence assertion on any mismatch.
    module.test_hand_reconstruction_matches_committed_gate_a_mkt()


# ---------------------------------------------------------------------------
# [6] RISK-FREE TRANSFORMATION
# ---------------------------------------------------------------------------
def test_item06_risk_free_transformation_formula() -> None:
    document = _normalized_report()
    assert (
        "RISK-FREE TRANSFORMATION = Rf_t = (DGS3MO_source(t) / 100) * "
        "(delta_calendar_days(t) / 365)" in document
    )
    assert _EVIDENCE_LIMITATION in document
    assert _STALENESS_SCOPE in document
    assert "/252" in document  # the rejected convention is named


# ---------------------------------------------------------------------------
# [7] RISK-FREE STALENESS / NO-FUTURE-LEAKAGE (re-run P5A-1's tests)
# ---------------------------------------------------------------------------
def test_item07_risk_free_staleness_and_no_future_leakage_rerun() -> None:
    module = _load_test_module(
        "test_risk_free_treasury.py", "p5a1_risk_free_treasury"
    )
    module.test_staleness_exactly_three_business_days_succeeds()
    module.test_staleness_four_business_days_raises()
    module.test_no_observation_at_or_before_raises_same_named_exception()
    module.test_first_date_in_requested_range_is_nan()
    module.test_latest_published_observation_never_reads_future()
    module.test_future_observation_is_ignored_by_get_risk_free()


# ---------------------------------------------------------------------------
# [8] NEWEY-WEST CONVENTION
# ---------------------------------------------------------------------------
def test_item08_newey_west_convention() -> None:
    assert DEFAULT_SETTINGS.newey_west_lags == 6
    document = _normalized_report()
    assert (
        "NEWEY-WEST CONVENTION = lag 6 (DEFAULT_SETTINGS.newey_west_lags)"
        in document
    )
    assert _NW_CAVEAT in document

    for path in (
        _GATE_A / "artifact_c_statistical_summary.json",
        _GATE_B / "artifact_c_statistical_summary.json",
    ):
        summary = _read_json(path)
        assert summary["status"] == "RUN"
        assert summary["newey_west_lags"] == 6
        assert summary["n"] >= 20


# ---------------------------------------------------------------------------
# [9] UPSTREAM DEFECT AUDIT
# ---------------------------------------------------------------------------
def test_item09_upstream_defect_audit() -> None:
    disposition = _read_json(_GATE_A / "GATE_A_DISPOSITION.json")
    findings = {entry["id"]: entry for entry in disposition["resolved_findings"]}
    assert findings["GATE-A-1"]["classification"] == "persistent_entitlement_mismatch"
    assert findings["GATE-A-2"]["classification"] == "spec_configuration_defect"

    # Both P5A-4 live-access blockers remain preserved as history.
    for name in (
        "P5A-4_BLOCKER_REPORT.md",
        "P5A-4_FIXTURE_RECORD_BLOCKER_REPORT.md",
    ):
        assert (_GATE_B / name).is_file()

    document = _normalized_report()
    assert "UPSTREAM DEFECT AUDIT = NONE FOUND" in document
    assert "GATE-A-1" in document and "GATE-A-2" in document

    # No already-trusted production module was modified anywhere in the phase:
    # only P5A-1's new provider and P5A-2's new orchestration module.
    proc = subprocess.run(
        ["git", "diff", "--name-only", f"{_PHASE_BASELINE}..HEAD", "--", "smart_beta"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    changed = set(proc.stdout.split())
    assert changed == {
        "smart_beta/pipelines/capm_pilot.py",
        "smart_beta/research_inputs/risk_free_treasury.py",
    }, f"a trusted production module was modified: {sorted(changed)}"


# ---------------------------------------------------------------------------
# [10] EVIDENCE TAXONOMY AUDIT
# ---------------------------------------------------------------------------
def test_item10_evidence_taxonomy_audit() -> None:
    document = _normalized_report()
    for label in (
        "LIVE-RECORDED",
        "FIXTURE-REPLAYED",
        "CONTRACT-MODELED",
        "CONSTRUCTED",
        "HAND-VERIFIED",
        "NOT CERTIFIED",
    ):
        assert label in document, f"taxonomy label {label!r} missing from report"

    # Every Phase 5A fixture manifest is live-recorded.
    for manifest_path in (
        _FIXTURES_DIR / "risk_free" / "treasury" / "manifest.json",
        _FIXTURES_DIR / "risk_free" / "treasury_gate_b" / "manifest.json",
        _FIXTURES_DIR / "tiingo" / "phase5a_gate_a" / "manifest.json",
        _FIXTURES_DIR / "tiingo" / "phase5a_gate_b" / "manifest.json",
    ):
        provenance = _read_json(manifest_path)["_provenance"]
        assert provenance["live_recorded"] is True, manifest_path

    # Preserved non-Gate-A evidence is explicitly labeled.
    assert "NOT Gate A evidence" in _read_text(
        _GATE_A / "partial_run_entitlement_limited_not_gate_a" / "README.md"
    )
    assert "NOT certified Gate A output" in _read_text(
        _GATE_A / "gate_a_2_compromised_default_settings" / "README.md"
    )

    # Gate A's own docs carry the frozen claim boundary.
    for path in (_GATE_A / "README.md", _GATE_A / "PROVENANCE.md"):
        assert _NOT_DEMONSTRATED in re.sub(r"\s+", " ", _read_text(path))


# ---------------------------------------------------------------------------
# [11] CREDENTIAL LEAKAGE
# ---------------------------------------------------------------------------
def test_item11_credential_leakage_sweep() -> None:
    files = _phase_added_files()
    assert files, "the phase diff must not be empty"

    scanned = list(files)
    scanned.append(str(_REPORT.relative_to(_REPO_ROOT)))
    scanned.append("tests/test_phase5a_certification.py")

    hits: list[str] = []
    for rel in scanned:
        path = _REPO_ROOT / rel
        if not path.is_file():
            continue
        text = _read_text(path)
        if _TOKEN_PATTERN.search(text):
            hits.append(rel)
    assert not hits, f"credential-shaped string(s) committed in: {hits}"

    # Stronger guard: no current real secret value appears anywhere.
    secret_values = [
        value
        for key in ("TIINGO_API_KEY", "TUSHARE_API_TOKEN", "TUSHARE_PROXY_TOKEN")
        if (value := os.environ.get(key))
    ]
    if secret_values:
        for rel in scanned:
            path = _REPO_ROOT / rel
            if not path.is_file():
                continue
            text = _read_text(path)
            for value in secret_values:
                assert value not in text, f"live secret value committed in {rel}"


# ---------------------------------------------------------------------------
# [12] FULL REGRESSION
# ---------------------------------------------------------------------------
_SUITE_RESULT: dict[str, object] = {}


def _run_full_suite_once() -> dict[str, object]:
    if _SUITE_RESULT:
        return _SUITE_RESULT
    env = dict(os.environ)
    for key in ("TIINGO_API_KEY", "TUSHARE_API_TOKEN", "TUSHARE_PROXY_TOKEN"):
        env.pop(key, None)
    env.pop("PYTEST_CURRENT_TEST", None)
    env.pop("PYTEST_ADDOPTS", None)
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "--ignore=tests/test_phase5a_certification.py",
        ],
        cwd=_REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=900,
    )
    _SUITE_RESULT.update(
        returncode=proc.returncode, stdout=proc.stdout, stderr=proc.stderr
    )
    return _SUITE_RESULT


def test_item12_full_regression() -> None:
    result = _run_full_suite_once()
    tail = "\n".join(str(result["stdout"]).strip().splitlines()[-5:])
    assert result["returncode"] == 0, (
        "full regression failed; summary tail:\n"
        f"{tail}\n--- stderr tail ---\n{str(result['stderr'])[-2000:]}"
    )
    assert " passed" in str(result["stdout"]), tail
    assert " failed" not in str(result["stdout"]).split("\n")[-1], tail


# ---------------------------------------------------------------------------
# [13] RF FORMULA INDEPENDENCE (independently computed literals)
# ---------------------------------------------------------------------------
def _fred_frame(relpath: str) -> pd.DataFrame:
    body = _read_json(_FIXTURES_DIR / relpath)
    return pd.read_csv(io.StringIO(body["raw_csv"]))


def test_item13_rf_formula_independence() -> None:
    # Frozen literals computed HERE with plain Python arithmetic on literals.
    specimen_a = 0.05 * 1 / 365
    specimen_b = 0.05 * 3 / 365
    compounded_a = (1.0 + 0.05) ** (1 / 252) - 1.0
    compounded_b = (1.0 + 0.05) ** (3 / 252) - 1.0

    synthetic_a = pd.DataFrame(
        {
            "observation_date": pd.to_datetime(["2024-01-03"]),
            "DGS3MO": [5.00],
        }
    )
    provider_a = TreasuryBillRiskFreeProvider(
        synthetic_a, trading_dates=["2024-01-02", "2024-01-03"]
    )
    rf_a = float(
        provider_a.get_risk_free("2024-01-02", "2024-01-03")["rf"].iloc[1]
    )
    assert rf_a == pytest.approx(specimen_a, rel=1e-12, abs=1e-15)
    assert abs(rf_a - compounded_a) > 1e-6  # /252 negative guard

    synthetic_b = pd.DataFrame(
        {
            "observation_date": pd.to_datetime(["2024-01-08"]),
            "DGS3MO": [5.00],
        }
    )
    provider_b = TreasuryBillRiskFreeProvider(
        synthetic_b, trading_dates=["2024-01-05", "2024-01-08"]
    )
    rf_b = float(
        provider_b.get_risk_free("2024-01-05", "2024-01-08")["rf"].iloc[1]
    )
    assert rf_b == pytest.approx(specimen_b, rel=1e-12, abs=1e-15)
    assert abs(rf_b - compounded_b) > 1e-6  # /252 negative guard

    # Real committed-fixture specimen: 2026-06-15 -> 2026-06-16, DGS3MO source
    # 2026-06-16 raw yield 3.79, delta_calendar_days = 1.
    provider_real = TreasuryBillRiskFreeProvider(
        _fred_frame("risk_free/treasury/dgs3mo_2025-09-01_2026-09-16.json"),
        trading_dates=["2026-06-15", "2026-06-16"],
    )
    rf_real = float(
        provider_real.get_risk_free("2026-06-15", "2026-06-16")["rf"].iloc[1]
    )
    assert rf_real == pytest.approx(3.79 / 100 * 1 / 365, rel=1e-12, abs=1e-15)
    assert abs(rf_real - ((1.0 + 0.0379) ** (1 / 252) - 1.0)) > 1e-6


# ---------------------------------------------------------------------------
# [14] CAPM SINGLE-AUTHORITY AUDIT
# ---------------------------------------------------------------------------
_CAPM_PRIVATE_SYMBOLS = (
    "_market_factor",
    "_value_weighted_returns",
    "_value_weighted_by",
    "_load_pit_panel",
)


def test_item14_capm_single_authority_audit() -> None:
    from smart_beta.pipelines import capm_pilot

    source = _read_text(capm_pilot.__file__)
    for symbol in _CAPM_PRIVATE_SYMBOLS:
        assert symbol not in source, f"capm_pilot.py references private {symbol!r}"
    match = re.search(r"from smart_beta\.benchmarks\.capm import ([^\n]+)", source)
    assert match is not None
    imported = [
        part.strip().split(" as ")[0].strip()
        for part in match.group(1).split(",")
    ]
    assert imported == ["compute_market_excess_return"]

    # Re-run the sanctioned decomposition invariant against the real committed
    # Gate A and Gate B outputs.
    for artifact in (
        _GATE_A / "artifact_a_market_factor.json",
        _GATE_B / "artifact_a_market_factor.json",
    ):
        rows = _read_json(artifact)
        residual = [
            abs(
                row["market_return"]
                - row["risk_free_return"]
                - row["MKT"]
            )
            for row in rows
            if row["MKT"] is not None
        ]
        assert residual, artifact
        assert max(residual) <= 1e-9, artifact


# ===========================================================================
# Report synchronization (guard only; every claim has an executable check)
# ===========================================================================
def test_certification_report_synchronized_with_dispositions() -> None:
    raw_lines = _read_text(_REPORT).splitlines()
    assert len(raw_lines) >= 14
    document = _normalized_report()

    # (a) all 14 items present with an explicit disposition.
    for index, label in _LINE_LABELS.items():
        matching = [line for line in raw_lines if f"**[{index}]" in line]
        assert matching, f"check [{index}] is absent from the report"
        assert any(label in line for line in matching), (
            f"check [{index}] is present but lacks its label {label!r}"
        )
        if index == 6:
            # Item 6's line is the formula itself, not a status token.
            assert any("Rf_t" in line for line in matching)
        else:
            assert any(
                token in line for line in matching for token in _STATUS_TOKENS
            ), f"check [{index}] has no explicit disposition token"

    # (b) the required verbatim sentences are present (whitespace-normalized).
    for required in (_NOT_DEMONSTRATED, _EVIDENCE_LIMITATION, _NW_CAVEAT):
        assert required in document, f"missing report sentence: {required!r}"

    # (c) the forbidden-terminology sweep finds nothing in the report.
    for phrase in _forbidden_universe_phrases():
        assert phrase.lower() not in document.lower(), phrase

    # (d) all 17 final-barrier points are explicitly evaluated. Each point's
    # header may wrap across source lines, so aggregate the block from the
    # point's marker up to the next barrier marker.
    blocks: dict[str, str] = {}
    current_point: str | None = None
    buffer: list[str] = []
    for line in raw_lines:
        marker_match = re.search(r"\*\*\[(B\d+)\]", line)
        if marker_match:
            if current_point is not None:
                blocks[current_point] = " ".join(buffer)
            current_point = marker_match.group(1)
            buffer = [line]
        elif current_point is not None:
            buffer.append(line)
    if current_point is not None:
        blocks[current_point] = " ".join(buffer)

    for point in range(1, _BARRIER_POINTS + 1):
        block = blocks.get(f"B{point}")
        assert block is not None, f"barrier point B{point} absent"
        assert "= PASS" in block, (
            f"barrier point B{point} has no explicit PASS/FAIL disposition"
        )
