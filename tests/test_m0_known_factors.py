"""C4M0-2 -- known-factor replication tests (synthetic fixtures only).

Authority: the frozen ``worker_tasks/campaign4_m0/campaign4-m0-plan.md``
section 3.3 (tests 1-4).  Every test is offline and synthetic: the store is a
``tmp_path`` Dataset-v1.2-shaped build, no production artifact / ``data_store``
/ reserve / holdout / Knowledge-PIT write / network path is touched.

The four frozen tests are:

1. **Direction oracle** -- a panel equal to the realized forward return gives a
   large positive long-short statistic; its negation gives a large negative
   one (the high-minus-low convention pinned end to end).
2. **Firewall** -- a non-IS fold raises ``Campaign3FirewallError`` before any
   panel is built, and no ``reserve_sealed`` path is opened (audited hook).
3. **Spec literals** -- KF-MOM12..KF-VSMOM hash-match the frozen
   ``campaign1_factorspecs.json`` entries and KF-LVOL certifies.
4. **Determinism** -- two runs produce byte-identical canonical JSON.
"""

from __future__ import annotations

import importlib.util
import json
import socket
import sys
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

import numpy as np

from smart_beta.campaign import campaign3_eval as E
from smart_beta.campaign.campaign1 import PERIODS_PER_YEAR as MONTHLY_PERIODS_PER_YEAR
from smart_beta.diagnostics import known_factors as KF

_REPO_ROOT = Path(__file__).resolve().parents[1]
_FIXTURE = _REPO_ROOT / "tests" / "fixtures" / "m0" / "synthetic.py"
_CAMPAIGN1 = _REPO_ROOT / "docs" / "campaign1_factorspecs.json"


def _load_fixture() -> Any:
    name = "m0_known_factors_fixture"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _FIXTURE)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


fx = _load_fixture()


@pytest.fixture(autouse=True)
def _no_sockets(monkeypatch: pytest.MonkeyPatch):
    """Fail closed if a test tries to open a socket."""

    def _refuse(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("C4M0-2 tests must never open a socket")

    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(socket, "create_connection", _refuse)
    yield


def _factor(factor_id: str) -> KF.KnownFactor:
    for factor in KF.build_known_factors():
        if factor.factor_id == factor_id:
            return factor
    raise KeyError(factor_id)


# ---------------------------------------------------------------------------
# 1. direction oracle
# ---------------------------------------------------------------------------


def test_direction_oracle_pins_high_minus_low(tmp_path: Path):
    _case, assembly = fx.build_case_and_assembly(tmp_path)
    factor = _factor("KF-MOM12")
    panel = fx.oracle_panel(assembly, factor)
    negated = panel.assign(value=-panel["value"])

    positive = KF.evaluate_known_factor(assembly, factor, panel=panel)
    negative = KF.evaluate_known_factor(assembly, factor, panel=negated)

    pos_b = positive.cell("B-T0")
    neg_b = negative.cell("B-T0")
    assert pos_b is not None and neg_b is not None
    # The factor *is* the realized return, so high-minus-low is large positive.
    assert pos_b.net_mean is not None and pos_b.net_mean > 0.0
    assert pos_b.hac_t is not None and pos_b.hac_t > 10.0
    # Its negation is large negative (the same convention, flipped).
    assert neg_b.net_mean is not None and neg_b.net_mean < 0.0
    assert neg_b.hac_t is not None and neg_b.hac_t < -10.0


def test_direction_oracle_is_a_different_panel_than_the_grammar(tmp_path: Path):
    _case, assembly = fx.build_case_and_assembly(tmp_path)
    factor = _factor("KF-MOM12")
    production = assembly.fast_panel_for(factor.spec, assembly.is_fold)
    oracle = fx.oracle_panel(assembly, factor)
    # The oracle panel replaces the grammar value with the realized return;
    # the row grid is identical (so the panel is a valid production panel).
    pd.testing.assert_frame_equal(
        production[["date", "stock_id"]].reset_index(drop=True),
        oracle[["date", "stock_id"]].reset_index(drop=True),
    )
    assert not production["value"].equals(oracle["value"])


# ---------------------------------------------------------------------------
# 2. firewall
# ---------------------------------------------------------------------------


def test_non_is_fold_raises_before_any_panel_is_built(tmp_path: Path, monkeypatch):
    case, assembly = fx.build_case_and_assembly(tmp_path)
    factor = _factor("KF-MOM12")
    built: list[Any] = []

    def _spy(*args: Any, **kwargs: Any) -> Any:
        built.append(args)
        raise AssertionError("a panel was built before the firewall check")

    monkeypatch.setattr(assembly, "fast_panel_for", _spy)
    holdout = case.partition.holdout_fold
    assert holdout is not None
    with pytest.raises(E.Campaign3FirewallError):
        KF.evaluate_known_factor(assembly, factor, fold=holdout)
    with pytest.raises(E.Campaign3FirewallError):
        KF.run_known_factors(assembly, fold=holdout, output_dir=tmp_path / "out")
    assert built == []


def test_run_never_opens_a_reserve_sealed_path(tmp_path: Path):
    _case, assembly = fx.build_case_and_assembly(tmp_path)
    opened: list[str] = []
    active = {"on": False}

    def hook(event: str, args: tuple[Any, ...]) -> None:
        if active["on"] and event == "open" and args:
            opened.append(str(args[0]))

    sys.addaudithook(hook)
    active["on"] = True
    try:
        KF.run_known_factors(assembly, output_dir=tmp_path / "out")
    finally:
        active["on"] = False
    assert not any("reserve_sealed" in path for path in opened)


# ---------------------------------------------------------------------------
# 3. spec literals
# ---------------------------------------------------------------------------


def test_campaign1_spec_hashes_match_the_frozen_register():
    entries = json.loads(_CAMPAIGN1.read_text(encoding="utf-8"))["specs"]
    expected = {
        "KF-MOM12": 0,
        "KF-MOM6": 1,
        "KF-REV1": 2,
        "KF-VSMOM": 5,
    }
    factors = {item.factor_id: item for item in KF.build_known_factors()}
    for factor_id, index in expected.items():
        assert factors[factor_id].spec_sha256 == entries[index][
            "factor_spec_canonical_sha256"
        ]


def test_planner_precheck_classes_hold():
    factors = {item.factor_id: item for item in KF.build_known_factors()}
    for factor_id in ("KF-MOM12", "KF-MOM6", "KF-REV1", "KF-VSMOM"):
        assert factors[factor_id].certified_class == "SIMPLE"
    assert factors["KF-LVOL"].certified_class == "SIMPLE"
    assert factors["KF-EAR"].certified_class == "EVENT_SIMPLE"


def test_build_known_factors_rejects_a_tampered_register(tmp_path: Path):
    payload = json.loads(_CAMPAIGN1.read_text(encoding="utf-8"))
    payload["specs"][0]["factor_spec_canonical_sha256"] = "0" * 64
    tampered = tmp_path / "campaign1_factorspecs.json"
    tampered.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(KF.KnownFactorSpecError):
        KF.build_known_factors(tampered)


# ---------------------------------------------------------------------------
# 4. determinism + report contract
# ---------------------------------------------------------------------------


def test_annualized_sr_uses_the_monthly_formation_annualization(tmp_path: Path):
    _case, assembly = fx.build_case_and_assembly(tmp_path)
    factor = _factor("KF-MOM12")
    diagnostic = KF.evaluate_known_factor(assembly, factor)
    cell = diagnostic.cell("B-T0")
    assert cell is not None

    # The SR annualizes the monthly-formation series with the frozen monthly
    # literal, not the declaration's 252-for-a-monthly-series value.
    assert cell.sr_periods_per_year == MONTHLY_PERIODS_PER_YEAR == 12
    assert cell.declaration_periods_per_year == 252

    net = fx.reconstructed_net_series(assembly, factor).to_numpy(dtype="float64")
    finite = net[np.isfinite(net)]
    expected = float(finite.mean() / finite.std(ddof=1) * np.sqrt(12.0))
    inflated = float(finite.mean() / finite.std(ddof=1) * np.sqrt(252.0))
    assert cell.annualized_sr == pytest.approx(expected)
    assert cell.annualized_sr != pytest.approx(inflated)


def test_two_runs_are_byte_identical(tmp_path: Path):
    _case, assembly = fx.build_case_and_assembly(tmp_path)
    first = KF.run_known_factors(assembly, output_dir=tmp_path / "first")
    second = KF.run_known_factors(assembly, output_dir=tmp_path / "second")
    first_bytes = (tmp_path / "first" / KF.REPORT_JSON_NAME).read_bytes()
    second_bytes = (tmp_path / "second" / KF.REPORT_JSON_NAME).read_bytes()
    assert first_bytes == second_bytes
    assert first.content_sha256 == second.content_sha256


def test_report_is_canonical_and_content_hashed(tmp_path: Path):
    _case, assembly = fx.build_case_and_assembly(tmp_path)
    report = KF.run_known_factors(assembly, output_dir=tmp_path / "out")
    path = tmp_path / "out" / KF.REPORT_JSON_NAME
    text = path.read_text(encoding="utf-8")
    # Canonical: no insignificant whitespace and a trailing newline only.
    assert text == KF._canonical_json(json.loads(text)) + "\n"
    payload = json.loads(text)
    stored = payload.pop("content_sha256")
    assert stored == KF._content_hash(payload)
    assert payload["schema"] == KF.SCHEMA
    assert [item["factor_id"] for item in payload["factors"]] == [
        "KF-MOM12",
        "KF-MOM6",
        "KF-REV1",
        "KF-VSMOM",
        "KF-LVOL",
        "KF-EAR",
    ]


def test_markdown_is_written_for_every_factor(tmp_path: Path):
    _case, assembly = fx.build_case_and_assembly(tmp_path)
    KF.run_known_factors(assembly, output_dir=tmp_path / "out")
    markdown = (tmp_path / "out" / KF.REPORT_MARKDOWN_NAME).read_text(
        encoding="utf-8"
    )
    for factor_id in (
        "KF-MOM12",
        "KF-MOM6",
        "KF-REV1",
        "KF-VSMOM",
        "KF-LVOL",
        "KF-EAR",
    ):
        assert factor_id in markdown


def test_factor_id_selection(tmp_path: Path):
    _case, assembly = fx.build_case_and_assembly(tmp_path)
    report = KF.run_known_factors(
        assembly, output_dir=tmp_path / "out", factor_ids=("KF-LVOL",)
    )
    assert [item.factor_id for item in report.factors] == ["KF-LVOL"]
    with pytest.raises(KF.KnownFactorSpecError):
        KF.run_known_factors(assembly, factor_ids=("KF-NOPE",))
