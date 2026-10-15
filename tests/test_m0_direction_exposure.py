"""C4M0-13 -- direction audit + common exposure tests (synthetic only).

Authority: the frozen
``worker_tasks/campaign4_m0/task-c4m0-13-direction-exposure.md`` section
"Tests (synthetic only)".  Every test is offline and synthetic: the persisted
generation / executor-state / screen-cache inputs and the factor panels are
hand-built under ``tmp_path``; no production artifact / ``data_store`` /
reserve / holdout / Knowledge-PIT write / network path is touched.

The seven frozen tests are:

1. **Spearman exposure** -- a candidate equal to a driver gives ``E = 1``, its
   negation ``-1``, an independent factor about ``0``; formations with fewer
   than 30 common members are skipped.
2. **Classification** -- CONSISTENT / INVERTED / WEAK / UNCODED, including the
   threshold boundaries.
3. **A4 replay** -- order-dependent; the first of a duplicate pair is kept and
   the second rejected; a negated copy is rejected.
4. **PCA** -- one common factor gives PC1 share > 0.9; independent series give
   a small share.
5. **Coding-file hash** -- a mismatch aborts before any data access.
6. **Firewall** -- a non-IS fold raises before any panel is built and no
   ``reserve_sealed`` path is opened (audited hook).
7. **Determinism** -- two runs produce byte-identical canonical JSON.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import socket
import sys
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from smart_beta.campaign import campaign3_eval as E
from smart_beta.campaign import campaign3_launch as L
from smart_beta.diagnostics import direction_exposure as DE
from smart_beta.evaluation.partition import Fold, FoldRole

_REPO_ROOT = Path(__file__).resolve().parents[1]
_FIXTURE = (
    _REPO_ROOT
    / "tests"
    / "fixtures"
    / "m0"
    / "direction_exposure"
    / "synthetic.py"
)
_SCRIPT = _REPO_ROOT / "scripts" / "run_m0_direction_exposure.py"

_IS_FOLD = Fold(FoldRole.IS, "2016-01-29", "2020-07-31")
_NON_IS_FOLD = Fold(FoldRole.OOS, "2020-07-31", "2020-12-31")


def _load_module(name: str, path: Path) -> Any:
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


fx = _load_module("m0_direction_exposure_fixture", _FIXTURE)


@pytest.fixture(autouse=True)
def _no_sockets(monkeypatch: pytest.MonkeyPatch):
    """Fail closed if a test tries to open a socket."""

    def _refuse(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("C4M0-13 tests must never open a socket")

    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(socket, "create_connection", _refuse)
    yield


def _run(fixture: Any, *, output_dir: Path | None = None, **overrides: Any) -> dict:
    kwargs: dict[str, Any] = {
        "generation_path": fixture.generation_path,
        "executor_state_path": fixture.executor_state_path,
        "screen_cache_dir": fixture.screen_cache_dir,
        "coding_path": fixture.coding_path,
        "coding_sha256": fixture.coding_sha256,
    }
    kwargs.update(overrides)
    if output_dir is not None:
        kwargs["output_dir"] = output_dir
    return DE.run_direction_exposure(fixture.assembly, **kwargs)


def _monthly_dates(count: int = 30) -> list[date]:
    return [
        date(2010 + index // 12, index % 12 + 1, 1) for index in range(count)
    ]


def _by_date(values: np.ndarray, dates: list[date]) -> dict[date, float]:
    return {day: float(value) for day, value in zip(dates, values)}


# ---------------------------------------------------------------------------
# 1. spearman exposure
# ---------------------------------------------------------------------------


def test_spearman_exposure_self_negation_and_independent():
    series = fx.momentum_series()
    panel = fx.panel_from_series(series)
    negated = fx.panel_from_series(fx.negate(series))
    independent = fx.panel_from_series(fx.random_series(seed=123))

    assert DE.spearman_exposure(panel, panel) == pytest.approx(1.0)
    assert DE.spearman_exposure(negated, panel) == pytest.approx(-1.0)
    assert abs(DE.spearman_exposure(independent, panel)) < 0.5


def test_spearman_exposure_skips_formations_below_the_minimum():
    series = fx.momentum_series()
    full = fx.panel_from_series(series)
    subset_30 = fx.panel_from_series(
        {stock: series[stock] for stock in fx.STOCKS[:30]}
    )
    subset_29 = fx.panel_from_series(
        {stock: series[stock] for stock in fx.STOCKS[:29]}
    )

    assert DE.spearman_exposure(full, full) == pytest.approx(1.0)
    assert DE.spearman_exposure(subset_30, full) == pytest.approx(1.0)
    # 29 common members < the section 1.3 minimum -> every formation skipped.
    assert DE.spearman_exposure(subset_29, full) is None


# ---------------------------------------------------------------------------
# 2. classification
# ---------------------------------------------------------------------------


def test_classification_includes_threshold_boundaries():
    assert DE.classify_exposure(0.9, 1) == "CONSISTENT"
    assert DE.classify_exposure(0.9, -1) == "INVERTED"
    assert DE.classify_exposure(0.20, 1) == "CONSISTENT"
    assert DE.classify_exposure(0.20, -1) == "INVERTED"
    assert DE.classify_exposure(-0.20, -1) == "CONSISTENT"
    assert DE.classify_exposure(-0.20, 1) == "INVERTED"
    assert DE.classify_exposure(0.19999, 1) == "WEAK"
    assert DE.classify_exposure(-0.19999, 1) == "WEAK"
    assert DE.classify_exposure(0.0, 1) == "WEAK"
    # No qualifying formation is treated as WEAK (no direction established).
    assert DE.classify_exposure(None, 1) == "WEAK"
    # An uncoded rationale (driver OTHER, or relation '?') is UNCODED.
    assert DE.classify_exposure(0.9, None) == "UNCODED"
    assert DE.classify_exposure(None, None) == "UNCODED"


# ---------------------------------------------------------------------------
# 3. the A4 replay
# ---------------------------------------------------------------------------


def test_a4_replay_is_order_dependent_and_catches_negation():
    similarity = {
        ("x", "y"): 0.95,
        ("x", "z"): -0.95,
        ("y", "z"): -0.95,
    }
    admitted, rejected = DE.a4_replay(("x", "y", "z"), similarity)
    assert admitted == ("x",)
    assert rejected == ("y", "z")

    admitted2, rejected2 = DE.a4_replay(("y", "x", "z"), similarity)
    assert admitted2 == ("y",)
    assert rejected2 == ("x", "z")


def test_a4_replay_threshold_is_strictly_greater():
    exact = {("x", "y"): DE.A4_THRESHOLD}
    admitted, rejected = DE.a4_replay(("x", "y"), exact)
    assert admitted == ("x", "y")
    assert rejected == ()

    above = {("x", "y"): DE.A4_THRESHOLD + 1e-9}
    admitted2, rejected2 = DE.a4_replay(("x", "y"), above)
    assert admitted2 == ("x",)
    assert rejected2 == ("y",)


# ---------------------------------------------------------------------------
# 4. PCA
# ---------------------------------------------------------------------------


def test_pca_one_factor_is_dominant_and_independent_is_small():
    dates = _monthly_dates(30)
    rng = np.random.default_rng(0)
    factor = rng.normal(size=len(dates))

    common: dict[str, dict[date, float]] = {}
    for index in range(6):
        noisy = factor + 0.01 * rng.normal(size=len(dates))
        common[f"k{index}"] = _by_date(noisy, dates)
    keys, correlation = DE.pearson_correlation_matrix(common)
    pc1_share, n_pcs, _eigenvalues = DE.principal_components(correlation)
    assert len(keys) == 6
    assert pc1_share > 0.9

    independent: dict[str, dict[date, float]] = {
        f"i{index}": _by_date(rng.normal(size=len(dates)), dates)
        for index in range(6)
    }
    _keys, corr_independent = DE.pearson_correlation_matrix(independent)
    pc1_independent, _n80, _eig = DE.principal_components(corr_independent)
    assert pc1_independent < 0.5
    assert n_pcs >= 1


# ---------------------------------------------------------------------------
# 5. coding-file hash
# ---------------------------------------------------------------------------


def test_coding_hash_mismatch_aborts_before_any_data_access(tmp_path: Path):
    fixture = fx.build_run_fixture(tmp_path, _IS_FOLD)
    # The other inputs deliberately do not exist: if the coding hash is checked
    # first, the mismatch is what surfaces.
    with pytest.raises(DE.CodingHashMismatch):
        DE.run_direction_exposure(
            fixture.assembly,
            generation_path=tmp_path / "missing_generation.json",
            executor_state_path=tmp_path / "missing_executor.json",
            screen_cache_dir=tmp_path / "missing_cache",
            coding_path=fixture.coding_path,
            coding_sha256="0" * 64,
        )


def test_coding_document_is_validated(tmp_path: Path):
    path = tmp_path / "coding.json"
    path.write_text(
        json.dumps(
            {
                "schema": DE.CODING_SCHEMA,
                "coding": {"a" * 64: {"driver": "NOPE", "relation": "+"}},
            }
        ),
        encoding="utf-8",
    )
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(DE.DirectionExposureSpecError):
        DE.load_coding(path, sha)


# ---------------------------------------------------------------------------
# 6. firewall
# ---------------------------------------------------------------------------


def test_non_is_fold_raises_before_any_panel_is_built(tmp_path: Path):
    fixture = fx.build_run_fixture(tmp_path, _IS_FOLD)
    calls: list[str] = []
    fixture.assembly.calls = calls
    with pytest.raises(E.Campaign3FirewallError):
        DE.run_direction_exposure(
            fixture.assembly,
            generation_path=tmp_path / "missing_generation.json",
            executor_state_path=tmp_path / "missing_executor.json",
            screen_cache_dir=tmp_path / "missing_cache",
            coding_path=fixture.coding_path,
            coding_sha256=fixture.coding_sha256,
            fold=_NON_IS_FOLD,
        )
    assert calls == []


def test_run_never_opens_a_reserve_sealed_path(tmp_path: Path):
    fixture = fx.build_run_fixture(tmp_path, _IS_FOLD)
    opened: list[str] = []
    active = {"on": False}

    def hook(event: str, args: tuple[Any, ...]) -> None:
        if active["on"] and event == "open" and args:
            opened.append(str(args[0]))

    sys.addaudithook(hook)
    active["on"] = True
    try:
        _run(fixture, output_dir=tmp_path / "out")
    finally:
        active["on"] = False
    assert not any("reserve_sealed" in path for path in opened)


# ---------------------------------------------------------------------------
# 7. determinism + the run contract
# ---------------------------------------------------------------------------


def test_two_runs_are_byte_identical(tmp_path: Path):
    fixture = fx.build_run_fixture(tmp_path, _IS_FOLD)
    first = _run(fixture, output_dir=tmp_path / "first")
    second = _run(fixture, output_dir=tmp_path / "second")
    first_bytes = (tmp_path / "first" / DE.REPORT_JSON_NAME).read_bytes()
    second_bytes = (tmp_path / "second" / DE.REPORT_JSON_NAME).read_bytes()
    assert first_bytes == second_bytes
    assert first["content_sha256"] == second["content_sha256"]


def test_report_is_canonical_and_content_hashed(tmp_path: Path):
    fixture = fx.build_run_fixture(tmp_path, _IS_FOLD)
    report = _run(fixture, output_dir=tmp_path / "out")
    path = tmp_path / "out" / DE.REPORT_JSON_NAME
    text = path.read_text(encoding="utf-8")
    assert text == DE._canonical_json(json.loads(text)) + "\n"
    payload = json.loads(text)
    stored = payload.pop("content_sha256")
    assert stored == DE._content_hash(payload)
    assert payload["schema"] == DE.SCHEMA
    assert payload["m0_1"]["reading"] == "UNDETERMINED"  # 2 coded < 20
    assert report["m0_3"]["reading"] in (
        "CONCENTRATED",
        "DIVERSE",
        "MODERATE",
    )


def test_run_classifies_consistent_inverted_and_weak(tmp_path: Path):
    fixture = fx.build_run_fixture(tmp_path, _IS_FOLD)
    report = _run(fixture)
    audits = {
        audit["proposal_id"]: audit
        for audit in report["m0_1"]["candidates"]
    }
    assert audits["a" * 64]["classification"] == "CONSISTENT"
    assert audits["a" * 64]["exposure"] == pytest.approx(1.0)
    assert audits["b" * 64]["classification"] == "INVERTED"
    assert audits["b" * 64]["exposure"] == pytest.approx(-1.0)
    assert audits["c" * 64]["classification"] == "WEAK"
    assert abs(audits["c" * 64]["exposure"]) < 0.5
    # The full section 1.3 E table is present for all five drivers.
    assert set(audits["a" * 64]["exposures"]) == set(DE.DRIVER_IDS)


def test_candidate_hash_mismatch_fails_closed(tmp_path: Path):
    fixture = fx.build_run_fixture(tmp_path, _IS_FOLD)
    payload = json.loads(fixture.generation_path.read_text(encoding="utf-8"))
    payload["candidates"][0]["factor_spec_hash"] = "0" * 64
    fixture.generation_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(DE.DirectionExposureSpecError):
        _run(fixture)


def test_markdown_is_written(tmp_path: Path):
    fixture = fx.build_run_fixture(tmp_path, _IS_FOLD)
    _run(fixture, output_dir=tmp_path / "out")
    markdown = (tmp_path / "out" / DE.REPORT_MARKDOWN_NAME).read_text(
        encoding="utf-8"
    )
    assert "M0-1 direction audit" in markdown
    assert "M0-3 common exposure" in markdown
    assert "D-MOM" in markdown


# ---------------------------------------------------------------------------
# frozen literals / launcher identity
# ---------------------------------------------------------------------------


def test_driver_precheck_classes_hold():
    classes = {
        driver.driver_id: driver.certified_class
        for driver in DE.build_driver_specs()
    }
    assert classes == {
        "D-MOM": "SIMPLE",
        "D-REV": "SIMPLE",
        "D-VOL": "SIMPLE",
        "D-DVT": "SIMPLE",
        "D-EAR": "EVENT_SIMPLE",
    }


def test_script_identity_reuses_the_c4m0_2_generator():
    script = _load_module("m0_direction_exposure_script", _SCRIPT)
    value = script.build_generator().prompt_template_hash
    expected = hashlib.sha256(b"c4m0-known-factors-diagnostic").hexdigest()
    assert value == expected
    assert len(value) == 64
    policy = L.build_production_policy(script.build_generator())
    assert policy.prompt_template_hash == expected
