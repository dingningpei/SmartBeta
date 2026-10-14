"""C4M0-4 -- TS terminal-stress decomposition tests (synthetic fixtures only).

Authority: the frozen
``worker_tasks/campaign4_m0/task-c4m0-4-ts-decomposition.md`` section 4.  Every
test is offline and synthetic: the store is a ``tmp_path`` Dataset-v1.2-shaped
build with real terminal-affected members, and no production artifact /
``data_store`` / reserve / holdout / Knowledge-PIT write / network path is
touched.

The five frozen tests are:

1. **The identity** -- on a synthetic panel with known affected members in both
   legs, ``gap_gross(f) == pred(f)`` to ``1e-12`` for every formation.
2. **No affected members** -- ``gap_gross == 0`` and ``pred == 0``.
3. **Firewall** -- a non-IS fold raises before any panel is built, and no
   ``reserve_sealed`` path is opened (audited hook).
4. **C3 spec reload** -- a tampered provenance spec (hash mismatch) fails
   closed; a valid one reproduces its recorded ``factor_spec_hash``.
5. **Determinism** -- two runs produce byte-identical canonical JSON.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import socket
import sys
from pathlib import Path
from typing import Any

import pytest

from smart_beta.campaign import campaign3_eval as E
from smart_beta.campaign import campaign3_launch as L
from smart_beta.diagnostics import known_factors as KF
from smart_beta.diagnostics import ts_decomposition as TD
from smart_beta.spec.factor_spec import factor_spec_hash
from smart_beta.spec.factor_spec import to_dict as factor_spec_to_dict

_REPO_ROOT = Path(__file__).resolve().parents[1]
_FIXTURE = _REPO_ROOT / "tests" / "fixtures" / "m0" / "ts" / "affected.py"
_SCRIPT = _REPO_ROOT / "scripts" / "run_m0_ts_decomposition.py"

_EXPECTED_FACTOR_ORDER = (
    "KF-MOM12",
    "KF-MOM6",
    "KF-REV1",
    "KF-VSMOM",
    "KF-LVOL",
    "KF-EAR",
)


def _load_module(name: str, path: Path) -> Any:
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


fx = _load_module("m0_ts_affected_fixture", _FIXTURE)


@pytest.fixture(autouse=True)
def _no_sockets(monkeypatch: pytest.MonkeyPatch):
    """Fail closed if a test tries to open a socket."""

    def _refuse(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("C4M0-4 tests must never open a socket")

    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(socket, "create_connection", _refuse)
    yield


def _load_script() -> Any:
    return _load_module("m0_ts_script", _SCRIPT)


def _valid_provenance(
    directory: Path,
    spec: Any,
    *,
    tamper: bool = False,
    digest: str = "a" * 64,
) -> Path:
    payload = {
        "schema": L.TA_PROVENANCE_SCHEMA,
        "diagnostics_hash": digest,
        "factor_spec_hash": (
            "0" * 64 if tamper else factor_spec_hash(spec)
        ),
        "factor_spec": factor_spec_to_dict(spec),
        "partition_fold": {
            "role": "IS",
            "start": "2020-01-01",
            "end": "2020-12-31",
        },
    }
    path = directory / f"{digest}.provenance.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# 1. the identity
# ---------------------------------------------------------------------------


def test_decomposition_identity_on_affected_panel(tmp_path: Path):
    _case, assembly = fx.build_case_and_assembly(tmp_path)
    factor = fx.known_factor("KF-MOM12")
    diagnostic = TD.evaluate_factor_decomposition(assembly, factor)

    assert diagnostic.assessed is True
    assert diagnostic.n_formations == 4
    saw_long = False
    saw_short = False
    for formation in diagnostic.formations:
        assert formation.n_affected_long + formation.n_affected_short > 0
        saw_long = saw_long or formation.n_affected_long > 0
        saw_short = saw_short or formation.n_affected_short > 0
        assert formation.gap_gross is not None
        assert formation.pred is not None
        # The frozen identity: the whole gross gap is the affected-member
        # tau = -/+1 contribution.
        assert abs(formation.gap_gross - formation.pred) <= 1e-12
    assert saw_long and saw_short, "fixture must place affected members in both legs"
    assert abs(diagnostic.mean_gap_gross - diagnostic.mean_pred) <= 1e-12


def test_e1_finite_denominator_with_nan_leg_members(tmp_path: Path):
    """ERRATUM E1: ``pred`` divides by each leg's FINITE-T0 count.

    The long leg is forced to hold both a non-admissible
    ``OBSERVED_PATH_INCOMPLETE`` member (``B``) and a non-admissible
    ``TERMINAL_OBSERVED_PATH_INCOMPLETE`` member (``E``); the accounting
    ``n_long`` counts all leg members while ``n_long_finite`` counts only the
    one with a finite T0 value.  A finite ``ADMISSIBLE_TERMINAL`` member
    (``A``) sits in the short leg, so the gap is genuinely non-zero and the
    identity still holds to 1e-12.
    """

    _case, assembly = fx.build_case_and_assembly(
        tmp_path,
        affected_keys=("A",),
        incomplete_keys=("B",),
        terminal_incomplete_keys=("E",),
    )
    factor = fx.known_factor("KF-MOM12")
    panel = fx.contoured_panel(
        assembly, factor, high_keys=("B", "E"), low_keys=("A",)
    )
    diagnostic = TD.evaluate_factor_decomposition(assembly, factor, panel=panel)
    assert diagnostic.assessed is True
    assert diagnostic.n_formations == 4
    for formation in diagnostic.formations:
        # The long leg counts the two NaN members in the D7b accounting ...
        assert formation.n_long > formation.n_long_finite
        assert formation.n_long - formation.n_long_finite == 2
        assert formation.n_long_finite >= 1
        # ... but not in the finite denominator.
        assert formation.n_short_finite == formation.n_short
        # E is terminal-affected in the long leg, yet its NaN T0 is excluded
        # from the E1 numerator (the finite affected member is A, in short).
        assert formation.n_affected_long >= 1
        assert formation.n_affected_short >= 1
        assert formation.gap_gross is not None
        assert formation.pred is not None
        assert abs(formation.gap_gross - formation.pred) <= 1e-12
        # The identity is non-trivial: a real affected member contributes.
        assert formation.gap_gross != 0.0
    assert abs(diagnostic.mean_gap_gross - diagnostic.mean_pred) <= 1e-12


def test_affected_members_get_tau_minus_and_plus_one(tmp_path: Path):
    _case, assembly = fx.build_case_and_assembly(tmp_path)
    factor = fx.known_factor("KF-MOM12")
    diagnostic = TD.evaluate_factor_decomposition(assembly, factor)
    for formation in diagnostic.formations:
        if formation.n_affected_long:
            # Long affected: T0 = r, TS = -1 (a -100% return).
            assert formation.long_ts is not None
        if formation.n_affected_short:
            # Short affected: T0 = r, TS = 1 + 2r.
            assert formation.short_ts is not None
    # The affected long leg is pushed deep negative under TS.
    assert diagnostic.mean_gap_gross is not None
    assert diagnostic.mean_gap_gross < 0.0


# ---------------------------------------------------------------------------
# 2. no affected members
# ---------------------------------------------------------------------------


def test_no_affected_members_gap_and_pred_are_zero(tmp_path: Path):
    _case, assembly = fx.build_case_and_assembly(tmp_path, affected_keys=())
    factor = fx.known_factor("KF-MOM12")
    diagnostic = TD.evaluate_factor_decomposition(assembly, factor)
    assert diagnostic.assessed is True
    assert diagnostic.n_formations == 4
    for formation in diagnostic.formations:
        assert formation.n_affected_long == 0
        assert formation.n_affected_short == 0
        assert formation.gap_gross == 0.0
        assert formation.pred == 0.0
    assert diagnostic.mean_gap_gross == 0.0
    assert diagnostic.mean_pred == 0.0
    assert diagnostic.excess_exposure is not None
    assert diagnostic.excess_exposure == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# 3. firewall
# ---------------------------------------------------------------------------


def test_non_is_fold_raises_before_any_panel_is_built(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    case, assembly = fx.build_case_and_assembly(tmp_path)
    factor = fx.known_factor("KF-MOM12")
    built: list[Any] = []

    def _spy(*args: Any, **kwargs: Any) -> Any:
        built.append(args)
        raise AssertionError("a panel was built before the firewall check")

    monkeypatch.setattr(assembly, "fast_panel_for", _spy)
    holdout = case.partition.holdout_fold
    assert holdout is not None
    with pytest.raises(E.Campaign3FirewallError):
        TD.evaluate_factor_decomposition(assembly, factor, fold=holdout)
    with pytest.raises(E.Campaign3FirewallError):
        TD.run_ts_decomposition(
            assembly,
            fold=holdout,
            output_dir=tmp_path / "out",
            factors=(factor,),
        )
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
        TD.run_ts_decomposition(assembly, output_dir=tmp_path / "out")
    finally:
        active["on"] = False
    assert not any("reserve_sealed" in path for path in opened)


# ---------------------------------------------------------------------------
# 4. C3 spec reload
# ---------------------------------------------------------------------------


def test_c3_spec_reload_reproduces_the_recorded_hash(tmp_path: Path):
    spec = KF.build_kf_lvol_spec()
    path = _valid_provenance(tmp_path, spec)
    loaded = TD.load_persisted_factor_spec(
        path, expected_diagnostics_hash="a" * 64
    )
    assert factor_spec_hash(loaded) == factor_spec_hash(spec)
    factors = TD.build_c3_candidate_factors([path])
    assert [factor.factor_id for factor in factors] == ["C3-IS-1"]
    assert factors[0].spec_sha256 == factor_spec_hash(spec)
    assert factors[0].certified_class == "SIMPLE"


def test_c3_spec_reload_hash_mismatch_fails_closed(tmp_path: Path):
    spec = KF.build_kf_lvol_spec()
    path = _valid_provenance(tmp_path, spec, tamper=True)
    with pytest.raises(TD.TSDecompositionSpecError):
        TD.load_persisted_factor_spec(path)
    with pytest.raises(TD.TSDecompositionSpecError):
        TD.build_c3_candidate_factors([path])


def test_c3_spec_reload_rejects_a_wrong_diagnostics_hash(tmp_path: Path):
    spec = KF.build_kf_lvol_spec()
    path = _valid_provenance(tmp_path, spec)
    with pytest.raises(TD.TSDecompositionSpecError):
        TD.load_persisted_factor_spec(
            path, expected_diagnostics_hash="b" * 64
        )


# ---------------------------------------------------------------------------
# 5. determinism + report contract
# ---------------------------------------------------------------------------


def test_two_runs_are_byte_identical(tmp_path: Path):
    _case, assembly = fx.build_case_and_assembly(tmp_path)
    first = TD.run_ts_decomposition(assembly, output_dir=tmp_path / "first")
    second = TD.run_ts_decomposition(assembly, output_dir=tmp_path / "second")
    first_bytes = (tmp_path / "first" / TD.REPORT_JSON_NAME).read_bytes()
    second_bytes = (tmp_path / "second" / TD.REPORT_JSON_NAME).read_bytes()
    assert first_bytes == second_bytes
    assert first.content_sha256 == second.content_sha256


def test_report_is_canonical_and_content_hashed(tmp_path: Path):
    _case, assembly = fx.build_case_and_assembly(tmp_path)
    report = TD.run_ts_decomposition(assembly, output_dir=tmp_path / "out")
    path = tmp_path / "out" / TD.REPORT_JSON_NAME
    text = path.read_text(encoding="utf-8")
    assert text == TD._canonical_json(json.loads(text)) + "\n"
    payload = json.loads(text)
    stored = payload.pop("content_sha256")
    assert stored == TD._content_hash(payload)
    assert payload["schema"] == TD.SCHEMA
    assert [item["factor_id"] for item in payload["factors"]] == list(
        _EXPECTED_FACTOR_ORDER
    )
    assert payload["reading"]["label"] in (
        "DECOMPOSITION_BROKEN",
        "TS_STRUCTURAL",
        "TS_FACTOR_SPECIFIC",
    )


def test_markdown_is_written_for_every_factor(tmp_path: Path):
    _case, assembly = fx.build_case_and_assembly(tmp_path)
    TD.run_ts_decomposition(assembly, output_dir=tmp_path / "out")
    markdown = (tmp_path / "out" / TD.REPORT_MARKDOWN_NAME).read_text(
        encoding="utf-8"
    )
    for factor_id in _EXPECTED_FACTOR_ORDER:
        assert factor_id in markdown


# ---------------------------------------------------------------------------
# 6. launcher script policy identity (the C4M0-2 R2 lesson)
# ---------------------------------------------------------------------------


def test_script_policy_identity_is_a_valid_sha256():
    """The script's diagnostic identity must satisfy the frozen policy gate.

    ``assemble_production`` builds the frozen ``ResearchPolicy`` from the
    injected generator's ``prompt_template_hash`` via
    ``build_production_policy``; a non-SHA-256 literal raises
    ``ResearchPolicyError`` before any panel is built.  This exercises that
    exact policy-construction path offline.
    """

    script = _load_script()
    value = script._DIAGNOSTIC_PROMPT_TEMPLATE_HASH
    assert value == hashlib.sha256(
        b"c4m0-ts-decomposition-diagnostic"
    ).hexdigest()
    assert len(value) == 64
    assert value == value.lower()
    int(value, 16)  # a 64-char lowercase hex string

    policy = L.build_production_policy(script.build_generator())
    assert policy.prompt_template_hash == value
